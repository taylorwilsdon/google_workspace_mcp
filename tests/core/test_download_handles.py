"""Tests for short claim-check download handles and the /dl route.

The handle store runs against the in-process memory fallback (no shared
backend configured), which exercises the same code paths the encrypted
shared store uses — only the backing store differs.
"""

import asyncio
import time

import pytest

from core import download_handles as dh

_CLAIMS = {
    "src": "gmail",
    "sub": "user@example.com",
    "iat": 1000,
    "exp": int(time.time()) + 900,
    "mid": "18c2f4a9b3d7e510",
    "aid": "ANGjdJ8" + "x" * 300,
}


@pytest.fixture(autouse=True)
def _reset_store(monkeypatch):
    from core import attachment_cred_cache as cache

    # Force the in-process MemoryStore fallback and clean singletons each test
    # (handles share the credential cache's store, so reset both modules).
    monkeypatch.delenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(cache, "_store", None)
    monkeypatch.setattr(cache, "_store_built", False)
    monkeypatch.setattr(dh, "_store", None)
    monkeypatch.setattr(dh, "_store_built", False)
    yield
    monkeypatch.setattr(cache, "_store", None)
    monkeypatch.setattr(cache, "_store_built", False)
    monkeypatch.setattr(dh, "_store", None)
    monkeypatch.setattr(dh, "_store_built", False)


class TestHandleStore:
    @pytest.mark.asyncio
    async def test_round_trip(self):
        handle = await dh.store_download_ref(_CLAIMS, ttl_seconds=60)
        assert handle is not None
        # 16 random bytes → 22-char urlsafe handle
        assert len(handle) == 22
        assert dh._HANDLE_RE.fullmatch(handle)

        claims = await dh.load_download_ref(handle)
        assert claims == _CLAIMS

    @pytest.mark.asyncio
    async def test_handles_are_unique(self):
        h1 = await dh.store_download_ref(_CLAIMS, ttl_seconds=60)
        h2 = await dh.store_download_ref(_CLAIMS, ttl_seconds=60)
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_unknown_handle_returns_none(self):
        assert await dh.load_download_ref("A" * 22) is None

    @pytest.mark.asyncio
    async def test_malformed_handles_rejected_before_store(self):
        for bad in ("", "short", "../../etc/passwd", "has space", "x" * 65, "a.b"):
            assert await dh.load_download_ref(bad) is None

    @pytest.mark.asyncio
    async def test_store_ttl_expires_handle(self):
        handle = await dh.store_download_ref(_CLAIMS, ttl_seconds=0.05)
        await asyncio.sleep(0.1)
        assert await dh.load_download_ref(handle) is None

    @pytest.mark.asyncio
    async def test_exp_claim_is_backstop(self):
        stale = dict(_CLAIMS, exp=int(time.time()) - 10)
        # Long store TTL, already-expired claim: the backstop must reject it.
        handle = await dh.store_download_ref(stale, ttl_seconds=600)
        assert await dh.load_download_ref(handle) is None

    @pytest.mark.asyncio
    async def test_missing_or_malformed_exp_fails_closed(self):
        # /dl/{handle} trusts loaded claims, so a record with no usable exp
        # must be rejected, not served indefinitely.
        no_exp = {k: v for k, v in _CLAIMS.items() if k != "exp"}
        handle = await dh.store_download_ref(no_exp, ttl_seconds=600)
        assert await dh.load_download_ref(handle) is None

        bad_exp = dict(_CLAIMS, exp="not-a-number")
        handle = await dh.store_download_ref(bad_exp, ttl_seconds=600)
        assert await dh.load_download_ref(handle) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_store_unavailable(self, monkeypatch):
        monkeypatch.setattr(dh, "_store", None)
        monkeypatch.setattr(dh, "_store_built", True)
        assert await dh.store_download_ref(_CLAIMS, ttl_seconds=60) is None
        assert await dh.load_download_ref("A" * 22) is None


class TestBuildDownloadUrl:
    @pytest.mark.asyncio
    async def test_short_url_by_default(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY", "test-key")
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://gw.example.com")
        from core.attachment_signing import build_download_url

        url = await build_download_url(
            source="gmail",
            user_email="user@example.com",
            ref={"mid": "18c2f4a9b3d7e510", "aid": "ANGjdJ8" + "x" * 300},
            ttl_seconds=900,
        )
        assert url.startswith("https://gw.example.com/dl/")
        assert len(url) < 60

        # The handle in the URL resolves back to the original claims.
        handle = url.rsplit("/", 1)[1]
        claims = await dh.load_download_ref(handle)
        assert claims["src"] == "gmail"
        assert claims["sub"] == "user@example.com"
        assert claims["aid"].startswith("ANGjdJ8")

    @pytest.mark.asyncio
    async def test_falls_back_to_jwt_when_store_unavailable(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY", "test-key")
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://gw.example.com")
        monkeypatch.setattr(dh, "_store", None)
        monkeypatch.setattr(dh, "_store_built", True)
        from core.attachment_signing import build_download_url, verify_attachment_token

        url = await build_download_url(
            source="drive",
            user_email="user@example.com",
            ref={"fid": "1A2b3C"},
            ttl_seconds=900,
        )
        assert url.startswith("https://gw.example.com/attachments/signed/")
        token = url.rsplit("/", 1)[1]
        claims = verify_attachment_token(token)
        assert claims["src"] == "drive"
        assert claims["fid"] == "1A2b3C"

    @pytest.mark.asyncio
    async def test_flag_forces_jwt_form(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY", "test-key")
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://gw.example.com")
        monkeypatch.setenv("WORKSPACE_MCP_SHORT_SIGNED_URLS", "false")
        from core.attachment_signing import build_download_url

        url = await build_download_url(
            source="drive",
            user_email="user@example.com",
            ref={"fid": "1A2b3C"},
            ttl_seconds=900,
        )
        assert "/attachments/signed/" in url


class TestShortDownloadRoute:
    def _request(self, handle: str):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/dl/{handle}",
            "raw_path": f"/dl/{handle}".encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("localhost", 8000),
            "path_params": {"handle": handle},
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        return Request(scope, receive)

    @pytest.mark.asyncio
    async def test_unknown_handle_403(self):
        from core.server import serve_short_download

        response = await serve_short_download(self._request("B" * 22))
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_handle_streams_via_fetcher(self, monkeypatch):
        from core.server import serve_short_download
        import core.signed_download as sd
        from core.signed_download import DownloadResult

        captured = {}

        async def fake_fetcher(claims, credentials):
            captured["claims"] = claims
            return DownloadResult(
                filename="report.pdf",
                media_type="application/pdf",
                content=b"%PDF-1.3",
            )

        class FakeCreds:
            valid = True
            refresh_token = None
            expiry = None

        class FakeSessionStore:
            def get_credentials(self, email):
                captured["email"] = email
                return FakeCreds()

        monkeypatch.setitem(sd._FETCHERS, "gmail", fake_fetcher)
        monkeypatch.setattr(
            "auth.oauth21_session_store.get_oauth21_session_store",
            lambda: FakeSessionStore(),
        )

        handle = await dh.store_download_ref(_CLAIMS, ttl_seconds=60)
        response = await serve_short_download(self._request(handle))

        assert response.status_code == 200
        assert captured["email"] == "user@example.com"
        assert captured["claims"]["mid"] == "18c2f4a9b3d7e510"
        assert "report.pdf" in response.headers["content-disposition"]
