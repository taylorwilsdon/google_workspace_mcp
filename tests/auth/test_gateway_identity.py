"""Tests for trusted-gateway identity assertion verification (auth/gateway_identity.py)."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import auth.gateway_identity as gi
from auth.oauth_config import OAuthConfig


class _Cfg:
    """Minimal stand-in for the OAuthConfig fields verify_gateway_assertion reads."""

    def __init__(
        self,
        jwks_url="https://gw/jwks.json",
        algs=None,
        aud="expected-aud",
        iss=None,
    ):
        self.gateway_identity_jwks_url = jwks_url
        self.gateway_identity_algorithms = algs or ["ES256"]
        self.gateway_identity_audience = aud
        self.gateway_identity_issuer = iss


class _SigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture
def ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, priv.public_key()


def _patch(monkeypatch, public_key, cfg):
    monkeypatch.setattr(gi, "get_oauth_config", lambda: cfg)

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _SigningKey(public_key)

    monkeypatch.setattr(gi, "_get_jwks_client", lambda url: _Client())


def _make(priv, **claims):
    payload = {
        "email": "andy@scientist.com",
        "exp": int(time.time()) + 300,
        "aud": "expected-aud",
    }
    payload.update(claims)
    return jwt.encode(payload, priv, algorithm="ES256")


def test_valid_assertion_returns_claims_and_email(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    token = _make(priv)
    claims = gi.verify_gateway_assertion(token)
    assert claims is not None and claims["email"] == "andy@scientist.com"
    assert gi.extract_email_from_assertion(token) == "andy@scientist.com"


def test_email_is_lowercased(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    token = _make(priv, email="Andy@Scientist.com")
    assert gi.extract_email_from_assertion(token) == "andy@scientist.com"


def test_principal_email_is_trimmed_validated_and_lowercased():
    assert gi.normalize_principal_email("  User@Example.COM  ") == "user@example.com"
    assert gi.normalize_principal_email("not-an-email") is None


def test_expired_token_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    token = _make(priv, exp=int(time.time()) - 10)
    assert gi.verify_gateway_assertion(token) is None


def test_wrong_signing_key_rejected(monkeypatch, ec_keypair):
    priv, _ = ec_keypair
    other_pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    _patch(monkeypatch, other_pub, _Cfg())
    assert gi.verify_gateway_assertion(_make(priv)) is None


def test_disallowed_algorithm_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg(algs=["RS256"]))  # token is ES256
    assert gi.verify_gateway_assertion(_make(priv)) is None


def test_audience_mismatch_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg(aud="expected-aud"))
    assert gi.verify_gateway_assertion(_make(priv, aud="wrong-aud")) is None


def test_audience_match_accepted(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg(aud="expected-aud"))
    assert gi.verify_gateway_assertion(_make(priv, aud="expected-aud")) is not None


def test_verified_but_emailless_extracts_none(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    token = jwt.encode(
        {
            "sub": "x",
            "exp": int(time.time()) + 300,
            "aud": "expected-aud",
        },
        priv,
        algorithm="ES256",
    )
    assert gi.verify_gateway_assertion(token) is not None  # signature/exp valid
    assert gi.extract_email_from_assertion(token) is None  # but no email claim


def test_empty_token_rejected(monkeypatch, ec_keypair):
    _, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    assert gi.verify_gateway_assertion("") is None


def test_missing_jwks_url_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg(jwks_url=None))
    assert gi.verify_gateway_assertion(_make(priv)) is None


def test_missing_audience_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg(aud=None))
    assert gi.verify_gateway_assertion(_make(priv)) is None


def test_blank_email_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    assert gi.extract_email_from_assertion(_make(priv, email="   ")) is None


def test_non_string_email_rejected(monkeypatch, ec_keypair):
    priv, pub = ec_keypair
    _patch(monkeypatch, pub, _Cfg())
    assert gi.extract_email_from_assertion(_make(priv, email=["a@b.com"])) is None


def test_require_gateway_principal_rejects_other_auth_sources():
    with pytest.raises(gi.GatewayIdentityError):
        gi.require_gateway_principal("user@example.com", "mcp_session_binding")


@pytest.mark.asyncio
async def test_get_verified_gateway_principal_reads_request_state():
    class _Context:
        async def get_state(self, key):
            return {
                "authenticated_user_email": " User@Example.com ",
                "authenticated_via": "gateway_assertion",
            }.get(key)

    principal = await gi.get_verified_gateway_principal(_Context())

    assert principal == "user@example.com"


def _gateway_config_env(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_IDENTITY", "true")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "false")
    monkeypatch.setenv("EXTERNAL_OAUTH21_PROVIDER", "false")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "false")
    monkeypatch.setenv("GATEWAY_IDENTITY_JWKS_URL", "https://gw/jwks.json")
    monkeypatch.setenv("GATEWAY_IDENTITY_AUDIENCE", "workspace-mcp")


def test_gateway_config_requires_audience(monkeypatch):
    _gateway_config_env(monkeypatch)
    monkeypatch.delenv("GATEWAY_IDENTITY_AUDIENCE", raising=False)

    with pytest.raises(ValueError, match="GATEWAY_IDENTITY_AUDIENCE"):
        OAuthConfig()


def test_gateway_config_accepts_explicit_audience(monkeypatch):
    _gateway_config_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_IDENTITY_AUDIENCE", "workspace-mcp")

    config = OAuthConfig()

    assert config.gateway_identity_audience == "workspace-mcp"


@pytest.mark.parametrize(
    "jwks_url",
    [
        "http://gateway.example/jwks.json",
        "ftp://gateway.example/jwks.json",
        "gateway.example/jwks.json",
    ],
)
def test_gateway_config_rejects_insecure_non_loopback_jwks_url(monkeypatch, jwks_url):
    _gateway_config_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_IDENTITY_JWKS_URL", jwks_url)

    with pytest.raises(ValueError, match="must use HTTPS"):
        OAuthConfig()


@pytest.mark.parametrize(
    "jwks_url",
    [
        "http://localhost/jwks.json",
        "http://127.0.0.1/jwks.json",
        "http://[::1]/jwks.json",
    ],
)
def test_gateway_config_allows_http_loopback_jwks_url(monkeypatch, jwks_url):
    _gateway_config_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_IDENTITY_JWKS_URL", jwks_url)

    config = OAuthConfig()

    assert config.gateway_identity_jwks_url == jwks_url


@pytest.mark.parametrize(
    "algorithms",
    [
        "HS256",
        "HS512",
        "none",
        "RS256,ES256",
        "RS256,PS256",
        "RS256,EdDSA",
        "unknown",
    ],
)
def test_gateway_config_rejects_unsafe_or_mixed_algorithm_families(
    monkeypatch, algorithms
):
    _gateway_config_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_IDENTITY_ALGORITHMS", algorithms)

    with pytest.raises(ValueError, match="asymmetric JWT"):
        OAuthConfig()


@pytest.mark.parametrize(
    "algorithms",
    [
        "ES256, ES384",
        "RS256, RS512",
        "PS256, PS512",
        "EdDSA",
    ],
)
def test_gateway_config_accepts_one_asymmetric_algorithm_family(
    monkeypatch, algorithms
):
    _gateway_config_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_IDENTITY_ALGORITHMS", algorithms)

    config = OAuthConfig()

    assert config.gateway_identity_algorithms == [
        algorithm.strip() for algorithm in algorithms.split(",")
    ]
