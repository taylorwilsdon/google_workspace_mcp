"""Signed download URLs: the token is the authorization, so these pin the security
properties — required claims, rejection of anything not minted here (including
tokens from the server's other key families), nothing readable in the link, a
hard ceiling on token age, a TTL that never outlives the credentials, mint and serve agreeing on "usable" with REAL google-auth
credentials, the route touching Google only after verification and only with the
token owner's credentials (recovered read-only, refreshed in memory), hardened
response headers, and bounded-memory Drive streaming.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import h11
import httpx
import pytest
import requests
from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from google.oauth2.credentials import Credentials

import core.signed_downloads as sd


async def _send(response, spec="2.3"):
    """Run a response the way the server does and return the body it sent. The
    default is ASGI spec 2.3, what uvicorn reports, which takes Starlette's
    disconnect-watching branch; its cleanup runs exactly as in production."""
    sent = []

    async def receive():
        await asyncio.Event().wait()  # the client never disconnects

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"spec_version": spec}, "method": "GET"}
    await response(scope, receive, send)
    return b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )


async def _serve(token):
    """``sd.serve`` followed by sending the response, as the server does."""
    response = await sd.serve(token)
    response.sent = await _send(response)
    return response


def _run(coro):
    """Run a coroutine from a sync test without clearing the default event loop
    (``asyncio.run`` does, which breaks later tests that ask for it)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


USER = "user@example.com"
SECRET = "client-secret-with-enough-entropy"
TOKEN_URI = "https://oauth2.googleapis.com/token"


@pytest.fixture(autouse=True)
def signing_material(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", SECRET)
    monkeypatch.delenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://mcp.example.com/")
    monkeypatch.setattr(sd, "get_transport_mode", lambda: "streamable-http")
    sd._signing_key.cache_clear()
    yield
    sd._signing_key.cache_clear()


def _mint(**overrides):
    kwargs = dict(
        source="gmail", user_email=USER, ref={"mid": "m1", "aid": "a1"}, ttl_seconds=60
    )
    kwargs.update(overrides)
    return sd.mint_url(**kwargs)


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "src": "gmail",
        "sub": USER,
        "iat": now,
        "exp": now + 60,
        "mid": "m",
        "aid": "a",
    }
    claims.update(overrides)
    return claims


def _craft(claims: dict, key: bytes | None = None, at: int | None = None) -> str:
    """A token the route did not mint: real (or given) key, arbitrary claims, and
    optionally a Fernet timestamp of ``at`` instead of now."""
    fernet = Fernet(key or sd._signing_key())
    payload = json.dumps(claims).encode()
    token = (
        fernet.encrypt(payload) if at is None else fernet.encrypt_at_time(payload, at)
    )
    return token.decode()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, as google-auth


def _credentials(seconds_left=3600, refresh_token=None, token="ya29.access"):
    """A real google-auth object, so ``.valid`` applies REFRESH_THRESHOLD for real."""
    return Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id="client-id",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        expiry=_now() + timedelta(seconds=seconds_left),
    )


class _ReadOnlyStores:
    """Session-store and credential-store doubles that record and REFUSE writes."""

    def __init__(self, session=None, persistent=None):
        self.session, self.persistent, self.writes = session, persistent, []
        self.session_lookups, self.persistent_lookups = [], []

    # OAuth21SessionStore surface used by the module
    def get_credentials(self, email):
        self.session_lookups.append(email)
        return self.session.get(email) if self.session else None

    def store_session(self, **kwargs):
        self.writes.append(("store_session", kwargs))
        raise AssertionError("the signed route must not write to the session store")

    # CredentialStore surface used by the module
    def get_credential(self, email):
        self.persistent_lookups.append(email)
        return self.persistent.get(email) if self.persistent else None

    def store_credential(self, email, credentials):
        self.writes.append(("store_credential", email))
        raise AssertionError("the signed route must not write to the credential store")


@pytest.fixture
def stores(monkeypatch):
    """Install the doubles behind the real lookup helper (not patching the helper)."""
    doubles = _ReadOnlyStores()
    monkeypatch.setattr(
        "auth.oauth21_session_store.get_oauth21_session_store", lambda: doubles
    )
    monkeypatch.setattr("auth.credential_store.get_credential_store", lambda: doubles)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    return doubles


@pytest.fixture
def fetcher(monkeypatch):
    """Records the credentials the route hands to Google."""
    seen = {}

    async def fake(claims, credentials):
        seen["claims"], seen["token"] = claims, credentials.token
        return sd.DownloadResult(
            filename="f.bin", media_type="application/octet-stream", content=b"ok"
        )

    monkeypatch.setitem(sd._FETCHERS, "gmail", fake)
    return seen


@pytest.fixture
def token_endpoint(monkeypatch):
    """Google's token endpoint, faked at the HTTP transport (requests.Session)."""
    calls = []
    outcome = {
        "status": 200,
        "body": {"access_token": "ya29.refreshed", "expires_in": 3600},
    }

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("data")))
        response = requests.Response()
        response.status_code = outcome["status"]
        response._content = json.dumps(outcome["body"]).encode()
        response.headers["content-type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls, outcome


class TestEnabledFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(sd.FLAG_ENV, raising=False)
        assert sd.enabled() is False
        offer = _run(sd.offer_url(USER, source="gmail", ref={}))
        assert not offer and offer.reason == ""

    def test_surrounding_whitespace_still_enables(self, monkeypatch):
        """A Helm value or .env line often carries a trailing newline or space."""
        monkeypatch.setenv(sd.FLAG_ENV, " true\n")
        assert sd.enabled() is True

    def test_on_when_true(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        assert sd.enabled() is True

    def test_off_on_stdio_even_when_set(self, monkeypatch):
        """The stdio callback server does not mount the route; a local server hands
        out file paths instead."""
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        monkeypatch.setattr(sd, "get_transport_mode", lambda: "stdio")
        assert sd.enabled() is False

    @pytest.mark.asyncio
    async def test_route_is_inert_when_disabled(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        token = _token(_mint())
        monkeypatch.delenv(sd.FLAG_ENV)
        monkeypatch.setattr(sd, "_recover_credentials", Mock())
        response = await _serve(token)
        assert response.status_code == 404
        sd._recover_credentials.assert_not_called()


class TestStartupLog:
    def test_flag_on_stdio_logs_once_that_it_is_ignored(self, monkeypatch, caplog):
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        with caplog.at_level(logging.WARNING, logger=sd.__name__):
            sd.log_if_ignored("stdio")
        notes = [r for r in caplog.records if "ignored" in r.getMessage()]
        assert len(notes) == 1
        assert sd.FLAG_ENV in notes[0].getMessage() and "stdio" in notes[0].getMessage()

    @pytest.mark.parametrize(
        "flag, transport", [(None, "stdio"), ("true", "streamable-http")]
    )
    def test_otherwise_silent(self, monkeypatch, caplog, flag, transport):
        if flag is None:
            monkeypatch.delenv(sd.FLAG_ENV, raising=False)
        else:
            monkeypatch.setenv(sd.FLAG_ENV, flag)
        with caplog.at_level(logging.DEBUG, logger=sd.__name__):
            sd.log_if_ignored(transport)
        assert caplog.records == []


class TestStartupValidation:
    """Flag on over streamable-http: refuse to start unless links can work. The
    autouse fixture supplies a valid external URL and key; each test removes one."""

    @pytest.fixture(autouse=True)
    def flag_on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    def test_service_account_mode_refuses(self, monkeypatch):
        """Domain-wide delegation stores no per-user credentials, so the route
        could never serve a link; say so at startup rather than on every call."""
        monkeypatch.setattr(
            "auth.oauth_config.is_service_account_enabled", lambda: True
        )
        with pytest.raises(ValueError, match="service-account"):
            sd.validate_startup("streamable-http")

    def test_notice_receives_the_base_url_line(self):
        seen = []
        sd.validate_startup("streamable-http", notice=seen.append)
        assert len(seen) == 1 and "/attachments/signed/*" in seen[0]

    def _no_key(self, monkeypatch):
        from auth import oauth_config

        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET")
        monkeypatch.setattr(
            oauth_config,
            "get_oauth_config",
            lambda: type(
                "C",
                (),
                {
                    "client_secret": None,
                    "is_service_account_enabled": lambda self: False,
                    "is_external_oauth21_provider": lambda self: False,
                },
            )(),
        )
        sd._signing_key.cache_clear()

    def test_valid_logs_the_base_url_once(self, caplog):
        with caplog.at_level(logging.INFO, logger=sd.__name__):
            sd.validate_startup("streamable-http")
        messages = [r.getMessage() for r in caplog.records]
        assert messages == [
            f"{sd.FLAG_ENV} is on: signed download links will use base URL "
            "https://mcp.example.com; /attachments/signed/* must be publicly "
            "reachable there."
        ]

    def test_missing_external_url_refuses(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_EXTERNAL_URL")
        with pytest.raises(ValueError) as exc:
            sd.validate_startup("streamable-http")
        assert str(exc.value) == (
            f"{sd.FLAG_ENV}=true requires WORKSPACE_EXTERNAL_URL; set it to the "
            "absolute http:// or https:// URL clients reach this server at "
            "(e.g. https://mcp.example.com)."
        )

    @pytest.mark.parametrize("value", ["", "  "])
    def test_empty_external_url_refuses(self, monkeypatch, value):
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", value)
        with pytest.raises(ValueError, match="requires WORKSPACE_EXTERNAL_URL"):
            sd.validate_startup("streamable-http")

    @pytest.mark.parametrize(
        "value",
        [
            "mcp.example.com",
            "/mcp",
            "//mcp.example.com",
            "ftp://mcp.example.com",
            "https://",
            " https://mcp.example.com",
            "https://mcp.example.com ",
        ],
    )
    def test_non_absolute_http_external_url_refuses(self, monkeypatch, value):
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", value)
        with pytest.raises(ValueError) as exc:
            sd.validate_startup("streamable-http")
        assert str(exc.value) == (
            f"Invalid WORKSPACE_EXTERNAL_URL={value!r} for {sd.FLAG_ENV}=true; "
            "expected an absolute http:// or https:// URL "
            "(e.g. https://mcp.example.com)."
        )

    @pytest.mark.parametrize(
        "value", ["http://10.0.0.5:8000", "https://mcp.example.com/"]
    )
    def test_absolute_http_or_https_is_accepted(self, monkeypatch, value):
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", value)
        sd.validate_startup("streamable-http")

    def test_no_key_material_refuses(self, monkeypatch):
        self._no_key(monkeypatch)
        with pytest.raises(ValueError) as exc:
            sd.validate_startup("streamable-http")
        assert str(exc.value) == (
            f"{sd.FLAG_ENV}=true requires signing key material; set "
            "GOOGLE_OAUTH_CLIENT_SECRET or FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY."
        )

    def test_key_check_is_the_minting_key_function(self, monkeypatch):
        """No parallel reimplementation: the check calls ``_signing_key``."""
        calls = []

        def fake_key():
            calls.append(1)
            raise RuntimeError("no key")

        fake_key.cache_clear = lambda: None  # the autouse fixture clears it
        monkeypatch.setattr(sd, "_signing_key", fake_key)
        with pytest.raises(ValueError, match="signing key material"):
            sd.validate_startup("streamable-http")
        assert calls == [1]

    def test_fastmcp_jwt_material_alone_is_enough(self, monkeypatch):
        self._no_key(monkeypatch)
        monkeypatch.setenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "material")
        sd.validate_startup("streamable-http")

    def test_one_line_per_problem(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_EXTERNAL_URL")
        self._no_key(monkeypatch)
        with pytest.raises(ValueError) as exc:
            sd.validate_startup("streamable-http")
        lines = str(exc.value).split("\n")
        assert len(lines) == 2
        assert "WORKSPACE_EXTERNAL_URL" in lines[0] and sd.FLAG_ENV in lines[0]
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in lines[1] and sd.FLAG_ENV in lines[1]

    @pytest.mark.parametrize(
        "flag, transport", [(None, "streamable-http"), ("true", "stdio")]
    )
    def test_flag_off_or_stdio_neither_checks_nor_logs(
        self, monkeypatch, caplog, flag, transport
    ):
        if flag is None:
            monkeypatch.delenv(sd.FLAG_ENV)
        monkeypatch.delenv("WORKSPACE_EXTERNAL_URL")
        self._no_key(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger=sd.__name__):
            sd.validate_startup(transport)
        assert caplog.records == []


class TestToken:
    def test_round_trip_carries_ref_owner_and_names(self):
        url = _mint(
            source="drive",
            ref={"fid": "F1", "emt": "application/pdf"},
            filename="Report.pdf",
            mime_type="application/pdf",
        )
        assert url.startswith("https://mcp.example.com/attachments/signed/")
        claims = sd.verify_token(_token(url))
        assert claims["src"] == "drive" and claims["sub"] == USER
        assert claims["fid"] == "F1" and claims["emt"] == "application/pdf"
        assert claims["fn"] == "Report.pdf" and claims["mt"] == "application/pdf"
        assert claims["exp"] - claims["iat"] == 60

    def test_non_ascii_filename_round_trips(self):
        claims = sd.verify_token(_token(_mint(filename="Résumé — 履歴書.pdf")))
        assert claims["fn"] == "Résumé — 履歴書.pdf"

    def test_nothing_in_the_link_is_readable(self):
        """Authenticated encryption, not a signature: the owner, the file name and
        the resource IDs must not appear in the token or in any decoding of it."""
        secrets = [
            "owner-zq7@example.com",
            "Payroll-Q3-x9k.pdf",
            "MSGID-8h2q",
            "ATTID-p4w7",
        ]
        url = _mint(
            user_email=secrets[0],
            ref={"mid": secrets[2], "aid": secrets[3]},
            filename=secrets[1],
        )
        token = _token(url)
        candidates = [token.encode()]
        segments = [token, *token.split(".")]
        for segment in segments:
            padded = segment + "=" * (-len(segment) % 4)
            for decoder in (base64.urlsafe_b64decode, base64.b64decode):
                try:
                    candidates.append(decoder(padded))
                except Exception:
                    pass
        assert len(candidates) >= 2  # the token itself decodes as urlsafe base64
        for blob in candidates:
            for secret in secrets:
                assert secret.encode() not in blob
                assert secret.encode("utf-16-le") not in blob
        assert sd.verify_token(token)["sub"] == secrets[0]  # still fully recoverable

    def test_every_byte_tampered_is_rejected(self):
        token = _token(_mint())
        for i in range(len(token)):
            flipped = "B" if token[i] != "B" else "C"
            assert sd.verify_token(token[:i] + flipped + token[i + 1 :]) is None, i

    @pytest.mark.parametrize("cut", [1, 2, 10, 32, 57])
    def test_truncated_token_rejected(self, cut):
        token = _token(_mint())
        assert sd.verify_token(token[:-cut]) is None
        assert sd.verify_token(token[cut:]) is None

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "garbage",
            "not.a.jwt",
            "a.b.c",
            "gAAAAA",
            "ééééééééééééé",
        ],
    )
    def test_garbage_and_non_ascii_rejected(self, bad):
        assert sd.verify_token(bad) is None

    def test_oversized_token_rejected_before_decrypting(self, monkeypatch):
        monkeypatch.setattr(sd, "Fernet", Mock(side_effect=AssertionError("touched")))
        assert sd.verify_token("A" * (sd._MAX_TOKEN_CHARS + 1)) is None

    def test_non_string_token_rejected(self):
        assert sd.verify_token(None) is None
        assert sd.verify_token(_token(_mint()).encode()) is None

    def test_appending_to_a_valid_token_rejected(self):
        token = _token(_mint())
        assert sd.verify_token(token + "x") is None
        assert sd.verify_token(token + "AAAA") is None
        # Re-padded and then extended is still rejected.
        padded = token + "=" * (-len(token) % 4)
        assert sd.verify_token(padded + "AAAA") is None

    def test_minted_links_carry_no_padding_and_verify_either_way(self):
        # A trailing '=' is easily lost when a link is copied or auto-linked, so
        # none is emitted; a client that restores the padding still works.
        for _ in range(200):
            token = _token(_mint())
            assert "=" not in token
            assert sd.verify_token(token) is not None
            padded = token + "=" * (-len(token) % 4)
            assert sd.verify_token(padded) is not None

    @pytest.mark.parametrize(
        "payload", [b"\xff\xfe\x00", b"[1, 2]", b"null", b'"sub"', b"", b"{"]
    )
    def test_real_key_but_not_a_claims_object_rejected(self, payload):
        token = Fernet(sd._signing_key()).encrypt(payload).decode()
        assert sd.verify_token(token) is None

    def test_wrong_key_rejected(self, monkeypatch):
        token = _token(_mint())
        monkeypatch.setenv(
            "GOOGLE_OAUTH_CLIENT_SECRET", "a-different-client-secret-value"
        )
        sd._signing_key.cache_clear()
        assert sd.verify_token(token) is None

    def test_expired_token_rejected(self):
        assert sd.verify_token(_token(_mint(ttl_seconds=-1))) is None
        assert sd.verify_token(_token(_mint(ttl_seconds=0))) is None  # exp == now

    @pytest.mark.parametrize("missing", ["exp", "sub", "iat"])
    def test_correctly_encrypted_token_missing_a_required_claim_rejected(self, missing):
        claims = _claims()
        del claims[missing]
        assert sd.verify_token(_craft(claims)) is None

    @pytest.mark.parametrize(
        "field, value",
        [
            ("sub", ""),
            ("sub", None),
            ("sub", 7),
            ("sub", ["u@example.com"]),
            ("iat", "1700000000"),
            ("iat", 1700000000.5),
            ("iat", None),
            ("exp", "9999999999"),
            ("exp", True),
            ("exp", None),
        ],
    )
    def test_required_claim_of_the_wrong_type_rejected(self, field, value):
        assert sd.verify_token(_craft(_claims(**{field: value}))) is None
        assert sd.verify_token(_craft(_claims())) is not None  # the control passes

    def test_future_iat_beyond_skew_rejected(self):
        now = int(time.time())
        far = _claims(iat=now + sd._CLOCK_SKEW_SECONDS + 5, exp=now + 900)
        assert sd.verify_token(_craft(far)) is None
        near = _claims(iat=now + sd._CLOCK_SKEW_SECONDS - 5, exp=now + 900)
        assert sd.verify_token(_craft(near)) is not None

    def test_age_ceiling_beats_a_far_future_exp(self):
        """Fernet's timestamp is the hard ceiling: a token minted longer ago than
        the maximum link lifetime (plus skew) is refused even if ``exp`` says
        otherwise, so a wrong ``exp`` can never extend a link."""
        now = int(time.time())
        claims = _claims(iat=now - 3600, exp=now + 10**6)
        too_old = _craft(claims, at=now - sd._MAX_TOKEN_AGE_SECONDS - 5)
        assert sd.verify_token(too_old) is None
        just_inside = _craft(claims, at=now - sd.URL_TTL_SECONDS)
        assert sd.verify_token(just_inside) is not None
        assert sd._MAX_TOKEN_AGE_SECONDS == sd.URL_TTL_SECONDS + sd._CLOCK_SKEW_SECONDS

    def test_ttl_ceiling_is_evaluated_at_verify_time(self, monkeypatch):
        """The same token: accepted now, refused once the clock passes the ceiling
        (``exp`` is far future, so only the Fernet timestamp can refuse it)."""
        now = int(time.time())
        token = _craft(_claims(exp=now + 10**6))
        assert sd.verify_token(token) is not None
        monkeypatch.setattr(
            sd.time, "time", lambda: now + sd._MAX_TOKEN_AGE_SECONDS + 1
        )
        assert sd.verify_token(token) is None

    def test_fernet_timestamp_is_iat(self):
        token = _token(_mint())
        claims = sd.verify_token(token)
        # The link drops Fernet's padding; restore it to read the raw token.
        padded = token + "=" * (-len(token) % 4)
        assert (
            Fernet(sd._signing_key()).extract_timestamp(padded.encode())
            == claims["iat"]
        )

    def test_ref_cannot_override_reserved_claims(self):
        with pytest.raises(ValueError, match="reserved"):
            _mint(ref={"fid": "F", "sub": "attacker@example.com"})

    def test_oversized_claims_refuse_to_mint(self):
        """Never a dead link: a token the route would refuse is not minted; the
        tool-side gate turns the ValueError into the standard download path."""
        with pytest.raises(ValueError, match="too large"):
            _mint(ref={"mid": "m", "aid": "a" * sd._MAX_TOKEN_CHARS})

    def test_no_key_material_fails_closed(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET")
        sd._signing_key.cache_clear()
        from auth.oauth_config import get_oauth_config

        monkeypatch.setattr(get_oauth_config(), "client_secret", None)
        with pytest.raises(RuntimeError):
            _mint()
        assert sd.verify_token("x.y.z") is None


class TestSigningKey:
    """Which material derives the key (derived only — no key setting of its own),
    that it is a well-formed Fernet key, and that this route's key family is
    isolated from the server's other derived keys (the OAuth proxy's JWT key and
    storage key)."""

    OTHER_SALTS = ["fastmcp-jwt-signing-key", "fastmcp-storage-encryption-key"]

    def test_key_derives_from_the_client_secret_under_this_modules_salt(self):
        assert sd._signing_key() == derive_jwt_key(
            high_entropy_material=SECRET, salt=sd._KEY_SALT
        )

    def test_key_is_exactly_32_bytes_in_fernet_encoding(self):
        key = sd._signing_key()
        assert len(base64.urlsafe_b64decode(key)) == 32
        Fernet(key)  # would raise on anything but 32 urlsafe-base64 bytes

    def test_fastmcp_jwt_key_material_beats_the_client_secret(self, monkeypatch):
        monkeypatch.setenv(
            "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "override-material"
        )
        sd._signing_key.cache_clear()
        assert sd._signing_key() == derive_jwt_key(
            low_entropy_material="override-material", salt=sd._KEY_SALT
        )
        token = _token(_mint())
        assert sd.verify_token(token) is not None
        monkeypatch.delenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY")
        sd._signing_key.cache_clear()
        # Key now derived from the client secret: the override-minted token must fail.
        assert sd.verify_token(token) is None

    @pytest.mark.parametrize("salt", OTHER_SALTS)
    def test_tokens_from_the_servers_other_key_families_are_rejected(self, salt):
        """Same client secret, the OAuth proxy's salts: a token under a FastMCP
        access-token key or the storage key must never verify as a download link."""
        other_key = derive_jwt_key(high_entropy_material=SECRET, salt=salt)
        assert other_key != sd._signing_key()
        assert sd.verify_token(_craft(_claims(), key=other_key)) is None
        assert sd.verify_token(_craft(_claims())) is not None  # the control passes

    @pytest.mark.parametrize("salt", OTHER_SALTS)
    def test_isolation_holds_with_the_fastmcp_jwt_material(self, monkeypatch, salt):
        material = "operator-supplied-jwt-signing-material"
        monkeypatch.setenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", material)
        sd._signing_key.cache_clear()
        other_key = derive_jwt_key(low_entropy_material=material, salt=salt)
        assert other_key != sd._signing_key()
        assert sd.verify_token(_craft(_claims(), key=other_key)) is None
        assert sd.verify_token(_token(_mint())) is not None


class TestUsability:
    """One predicate for mint and serve, evaluated on real google-auth credentials."""

    NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    THRESHOLD = sd.REFRESH_THRESHOLD.total_seconds()  # 225 s in google-auth 2.x

    def _creds(self, seconds_left, **kw):
        creds = _credentials(**kw)
        creds.expiry = (self.NOW + timedelta(seconds=seconds_left)).replace(tzinfo=None)
        return creds

    def test_google_auth_threshold_is_what_this_module_assumes(self):
        creds = _credentials()
        creds.expiry = _now() + timedelta(seconds=self.THRESHOLD + 5)
        assert creds.valid is True
        creds.expiry = _now() + timedelta(seconds=self.THRESHOLD - 5)
        assert creds.valid is False  # still 220 s on the clock, already "expired"

    def test_module_imports_without_googles_private_threshold(self, monkeypatch):
        """REFRESH_THRESHOLD is private google-auth API. A release without it must not
        make this module (imported by the Gmail and Drive tools) unimportable."""
        import importlib.util

        import google.auth._helpers

        monkeypatch.delattr(google.auth._helpers, "REFRESH_THRESHOLD", raising=False)
        spec = importlib.util.spec_from_file_location("_sd_copy", sd.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.REFRESH_THRESHOLD == timedelta(seconds=self.THRESHOLD)

    def test_non_refreshable_usable_until_threshold_not_expiry(self):
        creds = self._creds(3600)
        assert sd.usable_seconds(creds, now=self.NOW) == 3600 - self.THRESHOLD
        assert sd.usable_seconds(self._creds(200), now=self.NOW) < 0

    def test_refreshable_is_unbounded(self):
        creds = self._creds(-3600, refresh_token="1//r")  # long expired, refreshable
        assert sd.usable_seconds(creds, now=self.NOW) == float("inf")
        assert sd.clamp_ttl(creds, now=self.NOW) == sd.URL_TTL_SECONDS

    def test_refresh_token_alone_is_not_refreshable(self):
        creds = self._creds(200, refresh_token="1//r")
        creds._client_secret = None  # google-auth cannot refresh without it
        assert sd.usable_seconds(creds, now=self.NOW) < 0

    def test_no_access_token_and_no_refresh_is_unusable(self):
        assert sd.usable_seconds(self._creds(3600, token=None), now=self.NOW) == 0

    def test_unknown_expiry_without_refresh_is_unusable(self):
        """Google expires the token within the hour; with no expiry recorded and
        nothing to refresh with, no link can promise to outlive it."""
        creds = self._creds(0)
        creds.expiry = None
        assert sd.usable_seconds(creds, now=self.NOW) == 0
        assert sd.clamp_ttl(creds, now=self.NOW) == 0

    def test_unknown_expiry_with_refresh_is_unbounded(self):
        creds = self._creds(0, refresh_token="1//r")
        creds.expiry = None
        assert sd.clamp_ttl(creds, now=self.NOW) == sd.URL_TTL_SECONDS

    def test_far_expiry_capped_at_default(self):
        assert sd.clamp_ttl(self._creds(3600), now=self.NOW) == sd.URL_TTL_SECONDS

    def test_near_expiry_clamped_below_the_threshold_with_margin(self):
        # 400 s on the clock: usable for 175 s, URL gets 145 s.
        assert sd.clamp_ttl(self._creds(400), now=self.NOW) == 400 - 225 - 30

    @pytest.mark.parametrize("seconds", [300, 314])
    def test_below_the_minimum_lifetime_is_not_minted(self, seconds):
        """300 s on the clock would leave a 45 s link: too short to hand to an agent
        that has yet to fetch it, so the tool keeps its stored copy instead."""
        assert sd.clamp_ttl(self._creds(seconds), now=self.NOW) == 0

    def test_exactly_the_minimum_lifetime_is_minted(self):
        seconds = 225 + 30 + sd._MIN_TTL_SECONDS
        assert sd.clamp_ttl(self._creds(seconds), now=self.NOW) == sd._MIN_TTL_SECONDS

    def test_link_lifetime_follows_a_shorter_mcp_session(self, monkeypatch):
        monkeypatch.setenv(
            "WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS", "300"
        )
        assert sd.clamp_ttl(self._creds(3600), now=self.NOW) == 300

    def test_a_longer_mcp_session_does_not_extend_links(self, monkeypatch):
        monkeypatch.setenv(
            "WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS", "86400"
        )
        assert sd.clamp_ttl(self._creds(3600), now=self.NOW) == sd.URL_TTL_SECONDS

    @pytest.mark.parametrize("seconds", [-120, 0, 200, 255])
    def test_inside_threshold_or_margin_is_non_positive(self, seconds):
        assert sd.clamp_ttl(self._creds(seconds), now=self.NOW) <= 0

    def test_url_never_outlives_usability(self):
        for secs in (256, 300, 600, 3600):
            creds = self._creds(secs)
            assert sd.clamp_ttl(creds, now=self.NOW) < sd.usable_seconds(
                creds, now=self.NOW
            )

    def test_format_ttl(self):
        assert sd.format_ttl(45) == "45 seconds"
        assert sd.format_ttl(270) == "~4 minutes"
        assert sd.format_ttl(900) == "~15 minutes"


class TestCredentialRecovery:
    """Session store first, then the persistent store — the same order for the
    tool-side gate and the route, so what gets offered can be served."""

    def test_refreshable_session_skips_the_persistent_store(self, stores):
        stores.session = {
            USER: _credentials(token="ya29.session", refresh_token="1//s")
        }
        stores.persistent = {USER: _credentials(token="ya29.store")}
        assert sd._recover_credentials(USER).token == "ya29.session"
        assert stores.persistent_lookups == []

    def test_a_bare_session_token_does_not_shadow_a_refreshable_store_entry(
        self, stores
    ):
        """A session built from an access token alone must not win over stored
        credentials that can refresh: whichever the route can use longer wins."""
        stores.session = {USER: _credentials(seconds_left=200, token="ya29.session")}
        stores.persistent = {
            USER: _credentials(token="ya29.store", refresh_token="1//r")
        }
        assert sd._recover_credentials(USER).token == "ya29.store"

    def test_a_longer_lived_session_token_still_wins(self, stores):
        stores.session = {USER: _credentials(seconds_left=3600, token="ya29.session")}
        stores.persistent = {USER: _credentials(seconds_left=600, token="ya29.store")}
        assert sd._recover_credentials(USER).token == "ya29.session"

    def test_session_miss_falls_back_to_the_persistent_store(self, stores):
        stores.persistent = {USER: _credentials(token="ya29.store")}
        assert sd._recover_credentials(USER).token == "ya29.store"
        assert stores.session_lookups == [USER] and stores.persistent_lookups == [USER]

    def test_stateless_mode_never_consults_the_persistent_store(
        self, stores, monkeypatch
    ):
        monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
        stores.persistent = {USER: _credentials(token="ya29.store")}
        assert sd._recover_credentials(USER) is None
        assert stores.persistent_lookups == []

    def test_store_errors_mean_not_recoverable(self, stores, monkeypatch):
        monkeypatch.setattr(stores, "get_credential", Mock(side_effect=OSError("disk")))
        assert sd._recover_credentials(USER) is None

    def test_offer_url_uses_the_persistent_store_too(self, stores, monkeypatch):
        """Legacy / trusted-gateway mode: the session store is empty after a restart
        and credentials live only in the credential store — the tool must still mint."""
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        stores.persistent = {USER: _credentials()}
        offer = _run(sd.offer_url(USER, source="gmail", ref={"mid": "m", "aid": "a"}))
        assert offer.ttl == sd.URL_TTL_SECONDS
        assert sd.verify_token(_token(offer.url))["sub"] == USER
        assert stores.writes == []


class TestOfferUrl:
    """The tool-side gate: a URL is only issued when the route can serve it, and the
    TTL the caller shows is the real (clamped) one."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    @staticmethod
    def _offer(**ref):
        return _run(sd.offer_url(USER, source="gmail", ref=ref))

    def test_mints_with_clamped_ttl(self, monkeypatch):
        creds = _credentials(seconds_left=600)  # no refresh token: clamp applies
        monkeypatch.setattr(sd, "_recover_credentials", lambda email: creds)
        offer = self._offer(mid="m", aid="a")
        assert 600 - 225 - 30 - 2 <= offer.ttl <= 600 - 225 - 30
        assert sd.verify_token(_token(offer.url))["sub"] == USER

    def test_no_url_without_recoverable_credentials(self, monkeypatch):
        monkeypatch.setattr(sd, "_recover_credentials", lambda email: None)
        offer = self._offer()
        assert not offer and offer.reason == sd.NO_CREDENTIALS

    def test_no_url_when_token_inside_googles_refresh_threshold(self, monkeypatch):
        creds = _credentials(seconds_left=200)
        monkeypatch.setattr(sd, "_recover_credentials", lambda email: creds)
        offer = self._offer()
        assert not offer and offer.reason == sd.SHORT_LIVED_CREDENTIALS

    def test_no_url_when_no_key_can_be_derived(self, monkeypatch):
        monkeypatch.setattr(sd, "_credential_candidates", lambda e: [_credentials()])
        monkeypatch.setattr(
            sd, "_signing_key", Mock(side_effect=RuntimeError("no key"))
        )
        offer = self._offer()
        assert not offer and "could not create the link" in offer.reason

    def test_the_note_names_the_reason(self, monkeypatch):
        monkeypatch.setattr(sd, "_recover_credentials", lambda email: None)
        assert sd.NO_CREDENTIALS in sd.unavailable_note(self._offer())

    def test_a_long_filename_is_shortened_not_refused(self, monkeypatch):
        """Sender-controlled names must not push the token past the route's cap."""
        monkeypatch.setattr(sd, "_credential_candidates", lambda e: [_credentials()])
        offer = _run(
            sd.offer_url(
                USER,
                source="gmail",
                ref={"mid": "m", "aid": "a"},
                filename="x" * 9000 + ".pdf",
            )
        )
        name = sd.verify_token(_token(offer.url))["fn"]
        assert len(name) == sd._MAX_FILENAME_CHARS and name.endswith(".pdf")

    def test_credential_lookup_runs_off_the_event_loop(self, monkeypatch):
        import threading

        seen = {}

        def lookup(email):
            seen["thread"] = threading.current_thread()
            return _credentials()

        monkeypatch.setattr(sd, "_recover_credentials", lookup)
        assert self._offer(mid="m", aid="a")
        assert seen["thread"] is not threading.main_thread()


class TestMintAndServeAgree:
    """The reviewer's probe as a test: real credentials 200 s from expiry — inside
    google-auth's 225 s REFRESH_THRESHOLD, so ``.valid`` is already False."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    async def _offer(self):
        return await sd.offer_url(USER, source="gmail", ref={"mid": "m1", "aid": "a1"})

    @pytest.mark.asyncio
    async def test_no_refresh_token_neither_mints_nor_serves(self, stores, fetcher):
        stores.session = {USER: _credentials(seconds_left=200)}
        assert stores.session[USER].valid is False
        assert not await self._offer()
        response = await _serve(_token(_mint()))  # a link minted by force
        assert response.status_code == 401 and "token" not in fetcher

    @pytest.mark.asyncio
    async def test_refresh_token_mints_full_ttl_and_route_refreshes_in_memory(
        self, stores, fetcher, token_endpoint
    ):
        calls, _ = token_endpoint
        creds = _credentials(seconds_left=200, refresh_token="1//refresh")
        stores.session = {USER: creds}
        offer = await self._offer()
        assert offer.ttl == sd.URL_TTL_SECONDS

        response = await _serve(_token(offer.url))

        assert response.status_code == 200 and response.body == b"ok"
        assert fetcher["token"] == "ya29.refreshed"
        assert [(m, u) for m, u, _ in calls] == [("POST", TOKEN_URI)]
        assert b"grant_type=refresh_token" in calls[0][2]
        assert stores.writes == []  # refreshed credentials never reach storage

    @pytest.mark.asyncio
    async def test_refresh_failure_is_401_and_nothing_is_fetched(
        self, stores, fetcher, token_endpoint
    ):
        _, outcome = token_endpoint
        outcome.update(status=400, body={"error": "invalid_grant"})
        stores.session = {USER: _credentials(seconds_left=200, refresh_token="1//r")}
        offer = await self._offer()
        response = await _serve(_token(offer.url))
        assert response.status_code == 401 and "token" not in fetcher
        assert stores.writes == []

    @pytest.mark.asyncio
    async def test_still_valid_token_is_used_without_a_refresh(
        self, stores, fetcher, token_endpoint
    ):
        calls, _ = token_endpoint
        stores.session = {USER: _credentials(seconds_left=300, refresh_token="1//r")}
        offer = await self._offer()
        assert (await _serve(_token(offer.url))).status_code == 200
        assert fetcher["token"] == "ya29.access" and calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "seconds_left", [3600, 300, 256, 254, 240, 224, 200, 60, 0]
    )
    async def test_offer_and_route_agree_at_every_point_of_the_token_life(
        self, stores, fetcher, seconds_left
    ):
        """Never mint what the route would refuse. Only inside the safety margin
        plus the minimum link lifetime above google-auth's threshold may the tool
        decline a URL the route would still have served — that gap is on purpose."""
        stores.session = {USER: _credentials(seconds_left=seconds_left)}
        offered = bool(await self._offer())
        status = (await _serve(_token(_mint()))).status_code
        if offered:
            assert status == 200, (seconds_left, status)
        threshold = sd.REFRESH_THRESHOLD.total_seconds()
        declinable = sd._EXPIRY_MARGIN_SECONDS + sd._MIN_TTL_SECONDS + 1
        if not threshold < seconds_left <= threshold + declinable:
            assert offered == (status == 200), (seconds_left, status)


class TestServe:
    @pytest.fixture
    def collaborators(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")
        seen = {}

        async def fetcher(claims, credentials):
            seen["claims"], seen["credentials"] = claims, credentials
            return sd.DownloadResult(
                filename='rep"ort\r\n.pdf',
                media_type="application/pdf",
                content=b"%PDF-1.3",
            )

        creds = _credentials()

        def recover(email):
            seen.setdefault("emails", []).append(email)
            return creds if email == USER else None

        monkeypatch.setitem(sd._FETCHERS, "gmail", fetcher)
        monkeypatch.setattr(
            sd,
            "_credential_candidates",
            lambda email: [c] if (c := recover(email)) is not None else [],
        )
        seen["creds"] = creds
        return seen

    @pytest.mark.asyncio
    async def test_streams_with_the_token_owners_credentials(self, collaborators):
        response = await _serve(_token(_mint()))

        assert response.status_code == 200
        assert response.body == b"%PDF-1.3"
        assert collaborators["emails"] == [USER]
        assert collaborators["credentials"] is collaborators["creds"]
        assert collaborators["claims"]["mid"] == "m1"
        disposition = response.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        assert 'filename="report.pdf"' in disposition
        assert "filename*=UTF-8''rep%22ort%0D%0A.pdf" in disposition

    @pytest.mark.asyncio
    async def test_control_characters_never_reach_the_ascii_filename(
        self, collaborators, monkeypatch
    ):
        """Sender-chosen names: h11 (uvicorn's HTTP/1.1 layer) refuses a header
        value containing NUL, which would turn one download into a dropped
        connection instead of a 200."""

        async def fetcher(claims, credentials):
            return sd.DownloadResult(
                filename="bad\x00name\x7f\ttab\x01.txt",
                media_type="text/plain",
                content=b"x",
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", fetcher)
        response = await _serve(_token(_mint()))
        disposition = response.headers["content-disposition"]
        assert 'filename="badnametab.txt"' in disposition
        assert "filename*=UTF-8''bad%00name%7F%09tab%01.txt" in disposition
        # h11 accepts exactly what the route emits; the raw name it would not.
        h11.Response(
            status_code=200,
            headers=[
                (k.encode(), v.encode("latin-1")) for k, v in response.headers.items()
            ],
        )
        with pytest.raises(h11.LocalProtocolError):
            h11.Response(
                status_code=200,
                headers=[
                    (b"content-disposition", b'attachment; filename="bad\x00name.txt"')
                ],
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["garbage", "a.b.c", ""])
    async def test_invalid_token_is_403_before_any_lookup(self, collaborators, bad):
        response = await _serve(bad)
        assert response.status_code == 403
        assert "emails" not in collaborators and "claims" not in collaborators

    @pytest.mark.asyncio
    async def test_expired_token_is_403(self, collaborators):
        assert (await _serve(_token(_mint(ttl_seconds=-5)))).status_code == 403
        assert "claims" not in collaborators

    @pytest.mark.asyncio
    async def test_unknown_source_is_403(self, collaborators):
        assert (await _serve(_token(_mint(source="ftp")))).status_code == 403

    @pytest.mark.asyncio
    async def test_owner_without_credentials_is_401_and_nothing_is_fetched(
        self, collaborators
    ):
        response = await _serve(_token(_mint(user_email="other@example.com")))
        assert response.status_code == 401
        assert collaborators["emails"] == ["other@example.com"]
        assert "claims" not in collaborators

    @pytest.mark.asyncio
    async def test_unusable_credentials_are_401(self, collaborators):
        creds = collaborators["creds"]  # no refresh token
        creds.expiry = _now() + timedelta(seconds=100)
        assert creds.valid is False
        assert (await _serve(_token(_mint()))).status_code == 401
        assert "claims" not in collaborators

    @pytest.mark.asyncio
    async def test_fetch_failure_is_502(self, collaborators, monkeypatch):
        async def failing(claims, credentials):
            raise sd.SignedDownloadError("boom")

        monkeypatch.setitem(sd._FETCHERS, "gmail", failing)
        assert (await _serve(_token(_mint()))).status_code == 502

    @pytest.mark.asyncio
    async def test_every_response_is_nosniff_and_uncacheable(
        self, collaborators, monkeypatch
    ):
        """Sender-typed bytes on a public capability URL: success and every error."""
        responses = {
            "ok": await _serve(_token(_mint())),
            "403": await _serve("garbage"),
            "401": await _serve(_token(_mint(user_email="other@example.com"))),
        }

        async def failing(claims, credentials):
            raise sd.SignedDownloadError("boom")

        monkeypatch.setitem(sd._FETCHERS, "gmail", failing)
        responses["502"] = await _serve(_token(_mint()))
        monkeypatch.delenv(sd.FLAG_ENV)
        responses["404"] = await _serve(_token(_mint()))

        for name, response in responses.items():
            assert response.headers["x-content-type-options"] == "nosniff", name
            assert response.headers["cache-control"] == "no-store", name

    @pytest.mark.asyncio
    async def test_streamed_responses_carry_the_same_headers(
        self, collaborators, monkeypatch
    ):
        async def body():
            yield b"part"

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="v.mov", media_type="video/quicktime", stream=body()
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        response = await _serve(_token(_mint()))
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert 'filename="v.mov"' in response.headers["content-disposition"]


class _FakeDownloader:
    """MediaIoBaseDownload stand-in: appends one chunk per next_chunk, never seeks."""

    def __init__(self, fh, request, chunksize):
        self._fh, self._payload, self._pos, self._cs = fh, request.payload, 0, chunksize

    def next_chunk(self):
        nxt = self._payload[self._pos : self._pos + self._cs]
        self._fh.write(nxt)
        self._pos += len(nxt)
        # Like googleapiclient: the total comes from the response's Content-Range.
        status = Mock(total_size=len(self._payload))
        return status, self._pos >= len(self._payload)


class _FakeFiles:
    def __init__(self, calls, payload):
        self.calls, self.payload = calls, payload

    # Mirrors the real client's signature so a kwarg the API rejects fails here too.
    def get_media(self, fileId, supportsAllDrives=False):
        self.calls.append(("get_media", fileId, supportsAllDrives))
        return self

    def export_media(self, fileId, mimeType):
        self.calls.append(("export_media", fileId, mimeType))
        return self


class TestDriveFetcher:
    PAYLOAD = bytes(range(256)) * 200  # 51,200 bytes
    CHUNK = 8192

    @pytest.fixture
    def drive(self, monkeypatch):
        import gdrive.drive_tools as drive_tools

        calls = []
        files = _FakeFiles(calls, self.PAYLOAD)
        monkeypatch.setattr(sd, "build", lambda *a, **k: Mock(files=lambda: files))
        monkeypatch.setattr(sd, "MediaIoBaseDownload", _FakeDownloader)
        monkeypatch.setattr(drive_tools, "DOWNLOAD_CHUNK_SIZE", self.CHUNK)
        return calls

    @pytest.mark.asyncio
    async def test_streams_bounded_chunks_that_reassemble_exactly(self, drive):
        result = await sd._fetch_drive(
            {
                "fid": "F",
                "fn": "v.mov",
                "mt": "video/quicktime",
            },
            Mock(),
        )

        assert result.content is None and result.stream is not None
        assert result.length == len(self.PAYLOAD)
        chunks = [c async for c in result.stream]
        assert b"".join(chunks) == self.PAYLOAD
        assert len(chunks) > 1 and max(map(len, chunks)) <= self.CHUNK
        assert result.filename == "v.mov" and result.media_type == "video/quicktime"

    @pytest.mark.asyncio
    async def test_get_media_supports_shared_drives(self, drive):
        """Without supportsAllDrives=True Drive 404s on shared-drive files, so a
        minted URL would 502 on every fetch while the non-signed path works."""
        result = await sd._fetch_drive({"fid": "SHARED"}, Mock())
        async for _ in result.stream:
            pass
        assert drive == [("get_media", "SHARED", True)]

    @pytest.mark.asyncio
    async def test_export_uses_export_media(self, drive):
        result = await sd._fetch_drive({"fid": "DOC", "emt": "application/pdf"}, Mock())
        # Exports are buffered: Google caps them at 10 MB, and a failure anywhere
        # in the body then becomes an error status, never a short file.
        assert result.stream is None and result.content == self.PAYLOAD
        assert drive == [("export_media", "DOC", "application/pdf")]
        assert result.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_a_streamed_file_is_not_size_capped(self, drive, monkeypatch):
        """One chunk is in memory at a time, so the cap (which bounds buffered
        bodies) does not apply, as on the stored-copy path."""
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10")
        result = await sd._fetch_drive({"fid": "F"}, Mock())
        assert b"".join([c async for c in result.stream]) == self.PAYLOAD

    @pytest.mark.asyncio
    async def test_an_export_is_not_size_capped(self, drive, monkeypatch):
        """Google's 10 MB export limit already bounds the buffer, so a link is
        never minted that the size cap would then refuse."""
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10")
        result = await sd._fetch_drive({"fid": "D", "emt": "application/pdf"}, Mock())
        assert result.content == self.PAYLOAD

    @pytest.mark.asyncio
    async def test_unknown_total_streams_without_a_length(self, monkeypatch):
        import gdrive.drive_tools as drive_tools

        class NoTotal(_FakeDownloader):
            def next_chunk(self):
                _status, done = super().next_chunk()
                return Mock(total_size=None), done

        files = _FakeFiles([], b"abc")
        monkeypatch.setattr(sd, "build", lambda *a, **k: Mock(files=lambda: files))
        monkeypatch.setattr(sd, "MediaIoBaseDownload", NoTotal)
        monkeypatch.setattr(drive_tools, "DOWNLOAD_CHUNK_SIZE", 8)
        result = await sd._fetch_drive({"fid": "F"}, Mock())
        assert result.length is None

    @pytest.mark.asyncio
    async def test_missing_fid_and_first_chunk_failure_raise(self, monkeypatch):
        with pytest.raises(sd.SignedDownloadError):
            await sd._fetch_drive({}, Mock())
        monkeypatch.setattr(sd, "build", lambda *a, **k: Mock())
        monkeypatch.setattr(
            sd,
            "MediaIoBaseDownload",
            Mock(return_value=Mock(next_chunk=Mock(side_effect=OSError("403")))),
        )
        with pytest.raises(sd.SignedDownloadError):
            await sd._fetch_drive({"fid": "F"}, Mock())

    @pytest.fixture
    def fails_on_second_chunk(self, monkeypatch):
        """Drive answers the first chunk, then the connection to Google drops."""
        import gdrive.drive_tools as drive_tools

        class Flaky(_FakeDownloader):
            def next_chunk(self):
                if self._pos:
                    raise OSError("connection reset by Google")
                return super().next_chunk()

        files = _FakeFiles([], self.PAYLOAD)
        monkeypatch.setattr(sd, "build", lambda *a, **k: Mock(files=lambda: files))
        monkeypatch.setattr(sd, "MediaIoBaseDownload", Flaky)
        monkeypatch.setattr(drive_tools, "DOWNLOAD_CHUNK_SIZE", self.CHUNK)

    @pytest.mark.asyncio
    async def test_mid_stream_failure_raises_instead_of_ending_cleanly(
        self, fails_on_second_chunk
    ):
        """A generator that returns ends the chunked body normally, which the
        client reads as a complete file. It must raise."""
        result = await sd._fetch_drive({"fid": "F"}, Mock())
        received = []
        with pytest.raises(sd.SignedDownloadError):
            async for chunk in result.stream:
                received.append(chunk)
        assert b"".join(received) == self.PAYLOAD[: self.CHUNK]

    def test_mid_stream_failure_is_never_a_complete_200_over_http(
        self, fails_on_second_chunk, monkeypatch
    ):
        """Through the route on a real server, behind a BaseHTTPMiddleware as in
        production. Such middleware ends a chunked body cleanly after the app
        raises, so without a Content-Length the client would receive a short file
        as a complete 200; with it, the server drops the connection instead."""
        import socket
        import threading

        import uvicorn
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.routing import Route

        monkeypatch.setenv(sd.FLAG_ENV, "true")
        monkeypatch.setattr(sd, "_credential_candidates", lambda e: [_credentials()])

        class PassThrough(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await call_next(request)

        async def route(request):
            return await sd.serve(request.path_params["token"])

        app = Starlette(
            routes=[Route("/attachments/signed/{token}", route)],
            middleware=[Middleware(PassThrough)],
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    pytest.fail("uvicorn test server did not start")
                time.sleep(0.02)
            token = _token(_mint(source="drive", ref={"fid": "F"}))
            with pytest.raises(httpx.RemoteProtocolError):
                httpx.get(f"http://127.0.0.1:{port}/attachments/signed/{token}")
        finally:
            server.should_exit = True
            thread.join(5)


class TestGmailFetchers:
    @pytest.fixture(autouse=True)
    def _gmail_tools_loaded(self):
        import gmail.gmail_tools  # noqa: F401  (a server offering Gmail loads it)

    @pytest.mark.asyncio
    async def test_attachment_bytes_and_signed_in_names(self, monkeypatch):
        gmail = Mock()
        gmail.users().messages().attachments().get().execute.return_value = {
            "data": "aGVsbG8"
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)

        result = await sd._fetch_gmail_attachment(
            {"mid": "m", "aid": "a", "fn": "hi.txt", "mt": "text/plain"}, Mock()
        )

        assert result.content == b"hello"
        assert (result.filename, result.media_type) == ("hi.txt", "text/plain")
        get = gmail.users().messages().attachments().get
        assert get.call_args.kwargs == {"userId": "me", "messageId": "m", "id": "a"}

    @pytest.mark.asyncio
    async def test_attachment_errors_become_download_errors(self, monkeypatch):
        gmail = Mock()
        gmail.users().messages().attachments().get().execute.side_effect = OSError(
            "boom"
        )
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        assert caught.value.status == 502
        with pytest.raises(sd.SignedDownloadError):
            await sd._fetch_gmail_attachment({"mid": "m"}, Mock())

    @pytest.mark.asyncio
    async def test_empty_attachment_is_served_empty(self, monkeypatch):
        """A zero-byte attachment is a file, as the stored-copy path saves it."""
        gmail = Mock()
        gmail.users().messages().attachments().get().execute.return_value = {"data": ""}
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        result = await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        assert result.content == b""

    @pytest.mark.asyncio
    async def test_a_vanished_attachment_is_a_404_the_caller_can_act_on(
        self, monkeypatch
    ):
        from googleapiclient.errors import HttpError

        gmail = Mock()
        gmail.users().messages().attachments().get().execute.side_effect = HttpError(
            Mock(status=404, reason="Not Found"), b'{"error": {"message": "gone"}}'
        )
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        assert caught.value.status == 404

    @pytest.mark.asyncio
    async def test_the_api_client_is_closed(self, monkeypatch):
        gmail = Mock()
        gmail.users().messages().attachments().get().execute.return_value = {
            "data": "aGk"
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        gmail.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_export_eml_round_trip(self, monkeypatch):
        import base64

        raw = b"From: a@example.com\r\nSubject: hi\r\n\r\nbody\r\n"
        gmail = Mock()
        gmail.users().messages().get().execute.return_value = {
            "raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)

        result = await sd._fetch_gmail_message(
            {"mid": "m1", "fmt": "raw", "fn": "hi"}, Mock()
        )

        assert result.content == raw
        assert (result.filename, result.media_type) == ("hi.eml", "message/rfc822")

    @pytest.mark.asyncio
    async def test_html_export_without_html_is_named_as_text(self, monkeypatch):
        """The extension follows what was rendered, not what was asked for."""
        import base64

        gmail = Mock()
        gmail.users().messages().get().execute.return_value = {
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"plain only").decode()},
            }
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        result = await sd._fetch_gmail_message(
            {"mid": "m1", "fmt": "html", "fn": "Quarterly numbers"}, Mock()
        )
        assert result.filename == "Quarterly numbers.txt"
        assert result.media_type == "text/plain"

    @pytest.mark.asyncio
    async def test_empty_message_names_its_reason(self, monkeypatch):
        gmail = Mock()
        gmail.users().messages().get().execute.return_value = {"payload": {}}
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_gmail_message({"mid": "m1", "fmt": "text"}, Mock())
        assert caught.value.status == 422
        assert caught.value.public == sd._EMPTY_EXPORT

    @pytest.mark.asyncio
    async def test_message_export_rejects_bad_claims(self):
        with pytest.raises(sd.SignedDownloadError):
            await sd._fetch_gmail_message({"fmt": "raw"}, Mock())
        with pytest.raises(sd.SignedDownloadError):
            await sd._fetch_gmail_message({"mid": "m", "fmt": "pdf"}, Mock())

    @pytest.mark.asyncio
    async def test_attachment_over_the_file_limit_is_a_download_error(
        self, monkeypatch
    ):
        """The size checked at mint time is Google's declaration; the route enforces
        the limit on the bytes it actually got."""
        gmail = Mock()
        gmail.users().messages().attachments().get().execute.return_value = {
            "data": "aGVsbG8"  # b"hello", 5 bytes
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "4")
        with pytest.raises(sd.SignedDownloadError, match="file size limit"):
            await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "5")
        result = await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        assert result.content == b"hello"

    @pytest.mark.asyncio
    async def test_message_export_over_the_file_limit_is_a_download_error(
        self, monkeypatch
    ):
        """sizeEstimate is approximate: an understated one must not let an
        over-limit export through the route."""
        import base64

        raw = b"From: a@example.com\r\nSubject: hi\r\n\r\nbody\r\n"
        gmail = Mock()
        gmail.users().messages().get().execute.return_value = {
            "raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")
        }
        monkeypatch.setattr(sd, "build", lambda *a, **k: gmail)
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", str(len(raw) - 1))
        with pytest.raises(sd.SignedDownloadError, match="file size limit"):
            await sd._fetch_gmail_message({"mid": "m1", "fmt": "raw"}, Mock())
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", str(len(raw)))
        result = await sd._fetch_gmail_message({"mid": "m1", "fmt": "raw"}, Mock())
        assert result.content == raw


@pytest.fixture(autouse=True)
def _fresh_route_state(monkeypatch):
    """The route's refreshed-credential cache and download slots are per process;
    each test starts with neither."""
    monkeypatch.setattr(sd, "_refreshed", sd.OrderedDict())
    monkeypatch.setattr(sd, "_download_slots", None)
    monkeypatch.setattr(sd, "_owner_downloads", {})


class TestRouteResources:
    """What one public request may consume: a slot, an API client, a refresh."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    @pytest.mark.asyncio
    async def test_busy_route_answers_503_instead_of_queueing(
        self, stores, fetcher, monkeypatch
    ):
        stores.session = {USER: _credentials()}
        monkeypatch.setattr(sd, "_MAX_CONCURRENT_DOWNLOADS", 1)
        slots = sd._slots()
        await slots.acquire()  # one download already in flight
        try:
            response = await _serve(_token(_mint()))
        finally:
            slots.release()
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert "claims" not in fetcher
        assert (await _serve(_token(_mint()))).status_code == 200

    @pytest.mark.asyncio
    async def test_a_buffered_download_gives_its_slot_back(self, stores, fetcher):
        stores.session = {USER: _credentials()}
        for _ in range(sd._MAX_CONCURRENT_DOWNLOADS + 2):
            assert (await _serve(_token(_mint()))).status_code == 200
        assert not sd._slots().locked()

    @pytest.mark.asyncio
    async def test_a_stream_holds_its_slot_and_client_until_it_ends(
        self, stores, monkeypatch
    ):
        stores.session = {USER: _credentials()}
        closed = []

        async def chunks():
            yield b"a"
            yield b"b"

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="f.bin",
                media_type="application/octet-stream",
                stream=chunks(),
                close=lambda done: (closed.append(True), done()),
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        monkeypatch.setattr(sd, "_MAX_CONCURRENT_DOWNLOADS", 1)
        response = await sd.serve(_token(_mint()))
        assert sd._slots().locked() and closed == []
        assert await _send(response) == b"ab"
        assert closed == [True] and not sd._slots().locked()

    @pytest.mark.asyncio
    async def test_a_send_that_fails_mid_stream_still_releases(
        self, stores, monkeypatch
    ):
        """Starlette skips background tasks and leaves the body generator
        suspended when send raises; the slot and the client must not wait for
        garbage collection."""
        stores.session = {USER: _credentials()}
        closed = []

        async def chunks():
            yield b"a"
            yield b"b"

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="f.bin",
                media_type="application/octet-stream",
                stream=chunks(),
                close=lambda done: (closed.append(True), done()),
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        response = await sd.serve(_token(_mint()))

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message.get("body"):
                raise OSError("client connection reset")

        from starlette.requests import ClientDisconnect

        with pytest.raises((OSError, ClientDisconnect)):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert closed == [True] and sd._owner_downloads == {}

    @pytest.mark.asyncio
    async def test_a_refreshed_token_is_reused_not_refreshed_again(
        self, stores, fetcher, token_endpoint
    ):
        calls, _ = token_endpoint
        stores.session = {USER: _credentials(seconds_left=200, refresh_token="1//r")}
        for _ in range(3):
            assert (await _serve(_token(_mint()))).status_code == 200
        assert len(calls) == 1 and fetcher["token"] == "ya29.refreshed"
        assert stores.writes == []

    @pytest.mark.asyncio
    async def test_a_new_refresh_token_is_not_served_from_the_cache(
        self, stores, fetcher, token_endpoint
    ):
        """A re-consent replaces the refresh token; the old grant's copy is dropped."""
        calls, _ = token_endpoint
        stores.session = {USER: _credentials(seconds_left=200, refresh_token="1//a")}
        await _serve(_token(_mint()))
        stores.session = {USER: _credentials(seconds_left=200, refresh_token="1//b")}
        await _serve(_token(_mint()))
        assert len(calls) == 2


class TestToolModuleGate:
    """A token for a source whose tools this server never loaded is not served,
    and serving it never imports (and so registers) those tools."""

    @pytest.mark.asyncio
    async def test_gmail_export_without_gmail_tools_is_a_404(self, monkeypatch):
        import sys

        monkeypatch.delitem(sys.modules, "gmail.gmail_tools", raising=False)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_gmail_message({"mid": "m", "fmt": "raw"}, Mock())
        assert caught.value.status == 404
        assert "gmail.gmail_tools" not in sys.modules

    @pytest.mark.asyncio
    async def test_drive_without_drive_tools_is_a_404(self, monkeypatch):
        import sys

        monkeypatch.delitem(sys.modules, "gdrive.drive_tools", raising=False)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_drive({"fid": "f"}, Mock())
        assert caught.value.status == 404
        assert "gdrive.drive_tools" not in sys.modules

    def test_the_route_module_imports_no_tool_module(self):
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(sd.__file__).read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not {m for m in imported if m.startswith(("gmail.", "gdrive."))}


class TestLimitsLoggingAndStartup:
    """Per-owner limits, header and log hygiene, and startup refusals."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    @pytest.mark.asyncio
    async def test_one_owner_cannot_hold_every_slot(self, stores, monkeypatch):
        """A leaked link, or one slow client, is bounded per owner so other
        users keep their downloads."""
        stores.session = {USER: _credentials(), "other@example.com": _credentials()}
        started, gate = asyncio.Event(), asyncio.Event()

        async def slow(claims, credentials):
            started.set()
            await gate.wait()
            return sd.DownloadResult(filename="f", media_type="a/b", content=b"x")

        monkeypatch.setitem(sd._FETCHERS, "gmail", slow)
        held = [
            asyncio.ensure_future(_serve(_token(_mint())))
            for _ in range(sd._MAX_DOWNLOADS_PER_OWNER)
        ]
        await started.wait()
        await asyncio.sleep(0)
        assert (await sd.serve(_token(_mint()))).status_code == 503
        other = asyncio.ensure_future(
            _serve(_token(_mint(user_email="other@example.com")))
        )
        await asyncio.sleep(0)
        gate.set()
        assert [r.status_code for r in await asyncio.gather(*held, other)] == [200] * (
            sd._MAX_DOWNLOADS_PER_OWNER + 1
        )
        assert sd._owner_downloads == {}

    @pytest.mark.asyncio
    async def test_a_buffered_body_keeps_its_slot_until_sent(self, stores, fetcher):
        stores.session = {USER: _credentials()}
        response = await sd.serve(_token(_mint()))
        assert sd._owner_downloads == {USER: 1}
        await _send(response)
        assert sd._owner_downloads == {} and not sd._slots().locked()

    @pytest.mark.asyncio
    async def test_a_response_that_fails_to_build_gives_its_slot_back(
        self, stores, monkeypatch
    ):
        stores.session = {USER: _credentials()}

        async def streaming(claims, credentials):
            async def body():
                yield b"x"

            return sd.DownloadResult(filename="f", media_type="a/b", stream=body())

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        monkeypatch.setattr(
            sd, "_GuardedStreamingResponse", Mock(side_effect=ValueError)
        )
        with pytest.raises(ValueError):
            await sd.serve(_token(_mint()))
        assert sd._owner_downloads == {} and not sd._slots().locked()

    @pytest.mark.parametrize(
        "value, served",
        [
            ("application/pdf", "application/pdf"),
            ("text/plain; charset=utf-8", "text/plain; charset=utf-8"),
            ("application/vnd.évil", "application/octet-stream"),
            ("bad\r\ninjected: 1", "application/octet-stream"),
            ("", "application/octet-stream"),
        ],
    )
    def test_media_types_from_metadata_are_sanitised(self, value, served):
        assert sd._safe_media_type(value) == served

    @pytest.fixture
    def redacting(self):
        """Install redaction for one test, then put the process's factory back."""
        previous = logging.getLogRecordFactory()
        sd.install_access_log_redaction()
        yield
        logging.setLogRecordFactory(previous)

    @pytest.mark.parametrize(
        "msg, args",
        [
            (
                '%s "%s %s"',
                ("1.2.3.4", "GET", "/attachments/signed/gAAAAABsecretTokenXYZ"),
            ),
            ("%(p)s", {"p": "/attachments/signed/gAAAAABsecretTokenXYZ"}),
            ({"path": "/attachments/signed/gAAAAABsecretTokenXYZ"}, ()),
            ("/attachments/signed/%s", ("gAAAAABsecretTokenXYZ",)),
        ],
        ids=["access-log", "dict-args", "dict-msg", "split-across-msg-and-args"],
    )
    def test_no_message_shape_carries_a_token(self, redacting, msg, args):
        seen = []

        class Capture(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        target = logging.getLogger("signed.redaction.test")
        handler = Capture()
        target.addHandler(handler)  # added after install: still covered
        target.setLevel(logging.INFO)
        try:
            target.info(msg, *(args if isinstance(args, tuple) else (args,)))
        finally:
            target.removeHandler(handler)
        assert "secretToken" not in seen[0] and "<redacted>" in seen[0]

    def test_the_route_pattern_in_documentation_lines_is_left_alone(self):
        assert (
            sd._redact("/attachments/signed/* must be public")
            == "/attachments/signed/* must be public"
        )

    def test_startup_installs_redaction_and_survives_a_replaced_factory(self):
        previous = logging.getLogRecordFactory()
        try:
            sd.validate_startup("streamable-http")
            assert logging.getLogRecordFactory()._redacts_signed_links
            logging.setLogRecordFactory(logging.LogRecord)  # someone replaced it
            sd.install_access_log_redaction()
            assert logging.getLogRecordFactory()._redacts_signed_links
        finally:
            logging.setLogRecordFactory(previous)

    def test_external_provider_mode_refuses(self, monkeypatch):
        monkeypatch.setattr(
            "auth.oauth_config.is_external_oauth21_provider", lambda: True
        )
        with pytest.raises(ValueError, match="EXTERNAL_OAUTH21_PROVIDER"):
            sd.validate_startup("streamable-http")

    def test_a_session_shorter_than_any_link_refuses(self, monkeypatch):
        monkeypatch.setenv(
            "WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS", "30"
        )
        with pytest.raises(ValueError, match="none could ever be issued"):
            sd.validate_startup("streamable-http")

    def test_an_invalid_session_setting_is_ignored_quietly(self, monkeypatch, caplog):
        monkeypatch.setenv("WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS", "x")
        with caplog.at_level(logging.WARNING):
            assert sd._max_link_seconds() == sd.URL_TTL_SECONDS
        assert caplog.records == []

    @pytest.mark.asyncio
    async def test_gmail_attachments_need_gmail_tools_too(self, monkeypatch):
        import sys

        monkeypatch.delitem(sys.modules, "gmail.gmail_tools", raising=False)
        with pytest.raises(sd.SignedDownloadError) as caught:
            await sd._fetch_gmail_attachment({"mid": "m", "aid": "a"}, Mock())
        assert caught.value.status == 404

    @pytest.mark.asyncio
    async def test_cleanup_waits_for_a_chunk_still_being_fetched(
        self, stores, monkeypatch
    ):
        """However the response ended, the slot and the client are released only
        once the worker thread's chunk is done — by callback, not by awaiting,
        so no cancellation (anyio re-cancels every await) can skip the wait."""
        stores.session = {USER: _credentials()}
        chunk_task = asyncio.get_running_loop().create_future()
        closed = []

        async def chunks():
            yield b"a"

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="f.bin",
                media_type="application/octet-stream",
                stream=chunks(),
                # Like Drive: finish closing only once the pending chunk is done.
                close=lambda done: chunk_task.add_done_callback(
                    lambda _: (closed.append(True), done())
                ),
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        response = await sd.serve(_token(_mint()))
        await _send(response)
        assert closed == [] and sd._owner_downloads == {USER: 1}
        chunk_task.set_result(None)
        await asyncio.sleep(0)
        assert closed == [True] and sd._owner_downloads == {}

    @pytest.mark.asyncio
    async def test_a_cancelled_read_leaves_its_chunk_running(self, monkeypatch):
        """The Drive stream exposes the chunk its thread is fetching; cancelling
        the read (a disconnect) must not mark that chunk done early."""
        import threading

        import gdrive.drive_tools as drive_tools

        release_chunk = threading.Event()

        class Slow(_FakeDownloader):
            def next_chunk(self):
                if self._pos:
                    release_chunk.wait(5)
                return super().next_chunk()

        files = _FakeFiles([], b"x" * 32)
        monkeypatch.setattr(sd, "build", lambda *a, **k: Mock(files=lambda: files))
        monkeypatch.setattr(sd, "MediaIoBaseDownload", Slow)
        monkeypatch.setattr(drive_tools, "DOWNLOAD_CHUNK_SIZE", 8)
        monkeypatch.setattr(sd, "_close_service", lambda service: None)

        result = await sd._fetch_drive({"fid": "F"}, Mock())
        assert await result.stream.__anext__() == b"x" * 8
        reading = asyncio.ensure_future(result.stream.__anext__())
        await asyncio.sleep(0.05)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        done = asyncio.Event()
        result.close(done.set)
        await asyncio.sleep(0.05)
        assert not done.is_set()  # the worker thread still has the client
        release_chunk.set()
        await asyncio.wait_for(done.wait(), 5)


class TestCredentialChoice:
    def test_a_refreshable_store_entry_beats_a_bare_session_token(self, stores):
        """Even with minutes left on it: the bare token would fail mid-download
        once it expires, and would shorten or block links meanwhile."""
        stores.session = {USER: _credentials(seconds_left=300, token="ya29.session")}
        stores.persistent = {USER: _credentials(token="ya29.store", refresh_token="r")}
        assert sd._recover_credentials(USER).token == "ya29.store"

    def test_between_bare_tokens_the_longer_lived_wins(self, stores):
        stores.session = {USER: _credentials(seconds_left=600, token="ya29.session")}
        stores.persistent = {USER: _credentials(seconds_left=3000, token="ya29.store")}
        assert sd._recover_credentials(USER).token == "ya29.store"

    def test_a_spent_session_falls_back_to_the_store(self, stores):
        stores.session = {USER: _credentials(seconds_left=100, token="ya29.session")}
        stores.persistent = {USER: _credentials(token="ya29.store", refresh_token="r")}
        assert sd._recover_credentials(USER).token == "ya29.store"

    def test_a_failing_store_keeps_the_session_answer(self, stores, monkeypatch):
        stores.session = {USER: _credentials(seconds_left=100, token="ya29.session")}
        monkeypatch.setattr(stores, "get_credential", Mock(side_effect=OSError("db")))
        assert sd._recover_credentials(USER).token == "ya29.session"


class TestProductionPaths:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    @pytest.mark.asyncio
    async def test_a_disconnect_on_spec_2_3_releases_everything(
        self, stores, monkeypatch
    ):
        """uvicorn reports ASGI 2.3: Starlette watches receive() for a
        disconnect and cancels the body. Cleanup must still run."""
        stores.session = {USER: _credentials()}
        closed = []

        async def endless():
            while True:
                yield b"x"
                await asyncio.sleep(0.01)

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="f",
                media_type="a/b",
                stream=endless(),
                close=lambda done: (closed.append(True), done()),
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        response = await sd.serve(_token(_mint()))
        bodies = 0

        async def receive():
            while bodies < 2:
                await asyncio.sleep(0.01)
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal bodies
            if message["type"] == "http.response.body":
                bodies += 1

        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
        assert closed == [True] and sd._owner_downloads == {}

    @pytest.mark.asyncio
    async def test_a_failed_hand_off_closes_the_client_too(self, stores, monkeypatch):
        stores.session = {USER: _credentials()}
        closed = []

        async def body():
            yield b"x"

        async def streaming(claims, credentials):
            return sd.DownloadResult(
                filename="f",
                media_type="a/b",
                stream=body(),
                close=lambda done: (closed.append(True), done()),
            )

        monkeypatch.setitem(sd._FETCHERS, "gmail", streaming)
        monkeypatch.setattr(
            sd, "_GuardedStreamingResponse", Mock(side_effect=ValueError)
        )
        with pytest.raises(ValueError):
            await sd.serve(_token(_mint()))
        assert closed == [True] and sd._owner_downloads == {}


class TestCredentialFallback:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(sd.FLAG_ENV, "true")

    @pytest.mark.asyncio
    async def test_a_revoked_stored_grant_falls_back_to_the_session_token(
        self, stores, fetcher, token_endpoint
    ):
        """The stored grant ranks first because it can refresh; when its refresh
        token turns out to be revoked, the working session token serves the link."""
        _, outcome = token_endpoint
        outcome.update(status=400, body={"error": "invalid_grant"})
        stores.session = {USER: _credentials(seconds_left=3000, token="ya29.session")}
        stores.persistent = {
            USER: _credentials(seconds_left=100, token="ya29.old", refresh_token="1//x")
        }
        offer = await sd.offer_url(USER, source="gmail", ref={"mid": "m", "aid": "a"})
        response = await _serve(_token(offer.url))
        assert response.status_code == 200 and fetcher["token"] == "ya29.session"

    @pytest.mark.asyncio
    async def test_with_nothing_else_to_try_a_failed_refresh_is_401(
        self, stores, fetcher, token_endpoint
    ):
        _, outcome = token_endpoint
        outcome.update(status=400, body={"error": "invalid_grant"})
        stores.persistent = {
            USER: _credentials(seconds_left=100, token="ya29.old", refresh_token="1//x")
        }
        response = await _serve(_token(_mint()))
        assert response.status_code == 401 and "token" not in fetcher
