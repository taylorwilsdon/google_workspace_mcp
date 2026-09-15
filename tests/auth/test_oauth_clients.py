"""Tests for the multi-client OAuth registry and per-account client selection."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from auth.google_auth import (
    check_client_secrets,
    create_oauth_flow,
    handle_auth_callback,
    load_client_secrets_from_env,
    resolve_oauth_client,
    start_auth_flow,
)
from auth.oauth21_session_store import OAuth21SessionStore, SessionContext
from auth.oauth_types import OAuthVersionDetectionParams
from auth.oauth_clients import (
    OAuthClient,
    OAuthClientRegistryError,
    OAuthClientResolutionError,
    load_registry_from_env,
    parse_registry_document,
)
from auth.oauth_config import OAuthConfig

REGISTRY_ENV_VARS = (
    "GOOGLE_OAUTH_CLIENTS_FILE",
    "GOOGLE_OAUTH_CLIENTS",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
)

THREE_CLIENT_DOC = {
    "default": "personal",
    "clients": {
        "personal": {"client_id": "personal-id", "client_secret": "personal-secret"},
        "work": {"client_id": "work-id", "client_secret": "work-secret"},
        "contract": {"client_id": "contract-id"},
    },
    "domains": {"warnes.net": "personal", "example.com": "work"},
    "emails": {"someone@example.com": "contract"},
}


# A registry with several clients and NO default. This is the configuration
# every fail-open bug in this area hides in: there is nothing to fall back to,
# so any code that treats "could not resolve" as "use the default" ends up
# using something arbitrary instead of refusing.
NO_DEFAULT_DOC = {
    "clients": {
        "personal": {"client_id": "personal-id", "client_secret": "personal-secret"},
        "work": {"client_id": "work-id", "client_secret": "work-secret"},
    },
    "domains": {"example.com": "work"},
}

_FASTMCP_ENV_VARS = (
    "FASTMCP_SERVER_AUTH",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET",
    "FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL",
    "FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH",
)


@pytest.fixture(autouse=True)
def clear_registry_env(monkeypatch):
    """Every test starts from an unconfigured environment."""
    for name in REGISTRY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pinned_fastmcp_env(monkeypatch):
    """Stop OAuthConfig's _set_if_absent from leaking into the rest of the run.

    ``_apply_fastmcp_google_env`` writes FASTMCP_* straight into ``os.environ``
    when they are absent, and monkeypatch cannot undo a write it did not make.
    Pre-setting them makes every ``_set_if_absent`` a no-op, and monkeypatch
    restores these.
    """
    for name in _FASTMCP_ENV_VARS:
        monkeypatch.setenv(name, "pinned-by-test")


# --------------------------------------------------------------------------
# Document parsing and validation
# --------------------------------------------------------------------------


def test_parses_multi_client_document():
    registry = parse_registry_document(THREE_CLIENT_DOC)

    assert len(registry) == 3
    assert registry.keys == ["contract", "personal", "work"]
    assert registry.default.key == "personal"
    assert registry.get("work").client_secret == "work-secret"


def test_client_without_secret_is_public():
    registry = parse_registry_document(THREE_CLIENT_DOC)
    contract = registry.get("contract")

    assert contract.client_secret is None
    assert contract.is_public is True


def test_blank_secret_is_treated_as_public_not_empty_secret():
    # A blank value in config means "no secret", not "a secret that is the
    # empty string" — the latter would be sent to Google as a real credential.
    registry = parse_registry_document(
        {"clients": {"a": {"client_id": "a-id", "client_secret": "   "}}}
    )

    assert registry.get("a").client_secret is None
    assert registry.get("a").is_public is True


@pytest.mark.parametrize(
    "document, expected_message",
    [
        ({}, "non-empty 'clients'"),
        ({"clients": {}}, "non-empty 'clients'"),
        ({"clients": {"a": "not-an-object"}}, "must be an object"),
        ({"clients": {"a": {}}}, "missing a non-empty string 'client_id'"),
        ({"clients": {"a": {"client_id": "   "}}}, "missing a non-empty string"),
        (
            {"clients": {"a": {"client_id": "x", "client_secret": 42}}},
            "non-string 'client_secret'",
        ),
        (
            {"clients": {"a": {"client_id": "x"}}, "default": "missing"},
            "is not defined in 'clients'",
        ),
        (
            {"clients": {"a": {"client_id": "x"}}, "domains": {"d.com": "nope"}},
            "refers to undefined client",
        ),
        (
            {"clients": {"a": {"client_id": "x"}}, "emails": {"e@d.com": "nope"}},
            "refers to undefined client",
        ),
        ({"clients": {"a": {"client_id": "x"}}, "domains": []}, "must be an object"),
        ("not-a-dict", "must be a JSON object"),
    ],
)
def test_malformed_documents_are_rejected(document, expected_message):
    with pytest.raises(OAuthClientRegistryError) as excinfo:
        parse_registry_document(document)
    assert expected_message in str(excinfo.value)


def test_client_describe_never_leaks_the_secret():
    client = OAuthClient(key="work", client_id="work-id", client_secret="s3cr3t")
    description = client.describe()

    assert "s3cr3t" not in description
    assert "work" in description


def test_client_repr_never_leaks_the_secret():
    # describe() is only reached by code that chose to call it. The dataclass
    # repr is reached by everything else: "%r" logging, an f-string, a
    # traceback rendered with locals, or the repr of any container holding the
    # client. A secret that is safe in one and cleartext in the other is not
    # protected at all.
    client = OAuthClient(key="work", client_id="work-id", client_secret="s3cr3t")

    assert "s3cr3t" not in repr(client)
    assert "s3cr3t" not in str(client)
    assert "s3cr3t" not in f"{client}"
    assert "s3cr3t" not in f"{client!r}"
    # Containers render their members with repr(), so this is the logging path
    # that matters in practice.
    assert "s3cr3t" not in repr({"clients": [client]})
    assert "s3cr3t" not in repr(parse_registry_document(THREE_CLIENT_DOC).get("work"))
    # Still diagnosable: the key identifies which client without exposing it.
    assert "work" in repr(client)


def test_sibling_credential_dataclasses_keep_secrets_out_of_repr():
    # Bug-class sweep for the same defect: a dataclass field holding a
    # credential with the generated repr left live. These two were found by
    # grepping every @dataclass in the tree for credential-bearing fields.
    detection = OAuthVersionDetectionParams(
        client_id="public-client-id",
        client_secret="detection-secret",
        code_verifier="pkce-verifier",
    )
    rendered = repr(detection)
    assert "detection-secret" not in rendered
    assert "pkce-verifier" not in rendered
    assert "public-client-id" in rendered

    class _FakeAccessToken:
        def __repr__(self):
            return "AccessToken(token='bearer-token-value')"

    context = SessionContext(session_id="s1", auth_context=_FakeAccessToken())
    assert "bearer-token-value" not in repr(context)
    assert "s1" in repr(context)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_exact_email_match_wins_over_domain():
    registry = parse_registry_document(THREE_CLIENT_DOC)

    # someone@example.com is mapped explicitly to "contract" while the
    # example.com domain maps to "work"; the more specific rule must win.
    assert registry.resolve("someone@example.com").key == "contract"
    assert registry.resolve("other@example.com").key == "work"


def test_domain_match():
    registry = parse_registry_document(THREE_CLIENT_DOC)
    assert registry.resolve("greg@warnes.net").key == "personal"


def test_unmapped_account_falls_back_to_default():
    registry = parse_registry_document(THREE_CLIENT_DOC)
    assert registry.resolve("stranger@elsewhere.org").key == "personal"


def test_resolution_is_case_insensitive():
    registry = parse_registry_document(THREE_CLIENT_DOC)

    assert registry.resolve("GREG@WARNES.NET").key == "personal"
    assert registry.resolve("  SomeOne@Example.COM  ").key == "contract"


def test_multi_client_without_default_returns_none_for_unmapped_account():
    # No default means an unmapped account is a configuration error, not a
    # silent authorization against an arbitrary project.
    document = dict(THREE_CLIENT_DOC)
    document.pop("default")
    registry = parse_registry_document(document)

    assert registry.default is None
    assert registry.resolve("stranger@elsewhere.org") is None
    assert registry.resolve("greg@warnes.net").key == "personal"


def test_single_client_is_its_own_default_without_being_named():
    registry = parse_registry_document({"clients": {"solo": {"client_id": "solo-id"}}})

    assert registry.default.key == "solo"
    assert registry.resolve("anyone@anywhere.com").key == "solo"


# --------------------------------------------------------------------------
# Environment loading
# --------------------------------------------------------------------------


def test_loads_registry_from_file(tmp_path, monkeypatch):
    path = tmp_path / "clients.json"
    path.write_text(json.dumps(THREE_CLIENT_DOC), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS_FILE", str(path))

    registry = load_registry_from_env()

    assert len(registry) == 3
    assert registry.resolve("greg@warnes.net").key == "personal"


def test_loads_registry_from_inline_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))

    registry = load_registry_from_env()

    assert registry.resolve("other@example.com").key == "work"


def test_registry_file_takes_precedence_over_inline(tmp_path, monkeypatch):
    path = tmp_path / "clients.json"
    path.write_text(
        json.dumps({"clients": {"from-file": {"client_id": "file-id"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS_FILE", str(path))
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENTS",
        json.dumps({"clients": {"from-inline": {"client_id": "inline-id"}}}),
    )

    assert load_registry_from_env().keys == ["from-file"]


def test_malformed_registry_file_raises_rather_than_falling_back(tmp_path, monkeypatch):
    # Silently ignoring a broken registry would authorize accounts against
    # whichever client happened to remain configured.
    path = tmp_path / "clients.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS_FILE", str(path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "legacy-id")

    with pytest.raises(OAuthClientRegistryError) as excinfo:
        load_registry_from_env()
    assert "not valid JSON" in str(excinfo.value)


def test_missing_registry_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENTS_FILE", str(tmp_path / "does-not-exist.json")
    )

    with pytest.raises(OAuthClientRegistryError) as excinfo:
        load_registry_from_env()
    assert "could not read" in str(excinfo.value)


def test_legacy_single_client_env_still_works(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "legacy-secret")

    registry = load_registry_from_env()

    assert registry.keys == ["default"]
    assert registry.default.client_id == "legacy-id"
    assert registry.default.client_secret == "legacy-secret"
    assert registry.resolve("anyone@anywhere.com").key == "default"


def test_no_configuration_at_all_returns_none():
    assert load_registry_from_env() is None


# --------------------------------------------------------------------------
# OAuthConfig integration and backward compatibility
# --------------------------------------------------------------------------


def test_legacy_env_still_populates_client_id_and_secret(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "legacy-secret")

    cfg = OAuthConfig()

    assert cfg.client_id == "legacy-id"
    assert cfg.client_secret == "legacy-secret"
    assert cfg.is_configured() is True
    assert cfg.has_multiple_clients() is False


def test_config_exposes_default_client_of_a_registry(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))

    cfg = OAuthConfig()

    assert cfg.client_id == "personal-id"
    assert cfg.has_multiple_clients() is True
    assert cfg.get_client_for_email("other@example.com").key == "work"
    assert cfg.get_client_by_key("contract").client_id == "contract-id"
    assert cfg.get_client_by_key("nonexistent") is None


def test_environment_summary_lists_keys_but_no_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))

    summary = OAuthConfig().get_environment_summary()

    assert summary["registered_client_keys"] == ["contract", "personal", "work"]
    rendered = json.dumps(summary)
    assert "personal-secret" not in rendered
    assert "personal-id" not in rendered


def test_unconfigured_config_has_no_client(monkeypatch):
    cfg = OAuthConfig()

    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.get_client_for_email("anyone@anywhere.com") is None


# --------------------------------------------------------------------------
# Flow-level client selection
# --------------------------------------------------------------------------


@pytest.fixture
def registry_config(monkeypatch):
    """Point auth.google_auth at a freshly built three-client config."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)
    return cfg


def test_resolve_by_email(registry_config):
    assert resolve_oauth_client(user_google_email="other@example.com").key == "work"


def test_explicit_client_key_wins_over_email(registry_config):
    # The callback passes the recorded key; it must not be second-guessed by
    # whatever the email would resolve to now.
    client = resolve_oauth_client(
        client_key="contract", user_google_email="greg@warnes.net"
    )
    assert client.key == "contract"


def test_unknown_client_key_raises_instead_of_falling_back(registry_config):
    # Falling back to the default would exchange the code against a different
    # Cloud project and surface as an opaque 'invalid_client' from Google.
    with pytest.raises(ValueError) as excinfo:
        resolve_oauth_client(client_key="retired-client")

    assert "no longer registered" in str(excinfo.value)


def test_absent_client_key_uses_default(registry_config):
    assert resolve_oauth_client().key == "personal"


def test_client_secrets_shape_is_confidential_for_a_client_with_a_secret():
    config = load_client_secrets_from_env(
        OAuthClient(key="work", client_id="work-id", client_secret="work-secret")
    )

    assert "web" in config
    assert config["web"]["client_id"] == "work-id"
    assert config["web"]["client_secret"] == "work-secret"


def test_client_secrets_reflect_the_environment_at_call_time(monkeypatch):
    # Regression: resolving the default client through the cached OAuthConfig
    # singleton made this function return whatever client the process started
    # with, ignoring the current environment entirely.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "first-id")
    assert load_client_secrets_from_env()["installed"]["client_id"] == "first-id"

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "second-id")
    assert load_client_secrets_from_env()["installed"]["client_id"] == "second-id"


def test_client_secrets_are_none_when_environment_has_no_client():
    assert load_client_secrets_from_env() is None


def test_client_secrets_shape_is_installed_for_a_public_client():
    config = load_client_secrets_from_env(
        OAuthClient(key="contract", client_id="contract-id")
    )

    assert "installed" in config
    assert config["installed"]["client_id"] == "contract-id"
    assert config["installed"]["client_secret"] == ""


# --------------------------------------------------------------------------
# OAuth state round-trip
# --------------------------------------------------------------------------


def test_client_key_survives_serialization_to_the_state_file(tmp_path):
    # NARROW ON PURPOSE: this covers the store's serialization only. It was
    # previously named ..._survives_the_state_round_trip, which read as though
    # it joined the producer to the consumer; it does not, and both
    # `client_key=` arguments could be deleted with this test still green. The
    # seam itself is covered by
    # test_client_key_travels_from_start_auth_flow_to_the_callback_exchange.
    #
    # The callback recovers the client from the persisted state, so the value
    # has to survive serialization to disk, not merely live in memory.
    state_file = tmp_path / "oauth_states.json"
    writer = OAuth21SessionStore(oauth_state_file=str(state_file))
    writer.store_oauth_state("state-abc", code_verifier="verifier", client_key="work")

    reader = OAuth21SessionStore(oauth_state_file=str(state_file))
    state_info = reader.validate_and_consume_oauth_state("state-abc")

    assert state_info is not None
    assert state_info.get("client_key") == "work"
    assert state_info.get("code_verifier") == "verifier"


def test_state_written_without_a_client_key_reads_back_as_none(tmp_path):
    # State entries created before multi-client support must still complete;
    # the callback treats a missing key as "the default client".
    state_file = tmp_path / "oauth_states.json"
    writer = OAuth21SessionStore(oauth_state_file=str(state_file))
    writer.store_oauth_state("state-legacy", code_verifier="verifier")

    reader = OAuth21SessionStore(oauth_state_file=str(state_file))
    state_info = reader.validate_and_consume_oauth_state("state-legacy")

    assert state_info is not None
    assert state_info.get("client_key") is None


# --------------------------------------------------------------------------
# OAuth 2.1 single-provider warning
# --------------------------------------------------------------------------


def _warnings_from(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


def _multi_client_warnings(caplog):
    return [
        message
        for message in _warnings_from(caplog)
        if "OAuth 2.1" in message and "OAuth client" in message
    ]


def test_oauth21_multi_client_warning_fires_when_there_is_no_default(
    monkeypatch, caplog, pinned_fastmcp_env
):
    # The no-default case is the ONE case where this warning is the only
    # signal an operator gets, and it was exactly the case the warning could
    # not reach: `if not self.client_id: return` ran first, and client_id is
    # None precisely when there is no default client.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(NO_DEFAULT_DOC))
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    with caplog.at_level(logging.WARNING, logger="auth.oauth_config"):
        config = OAuthConfig()

    assert config.client_id is None  # the condition that suppressed the warning
    matched = _multi_client_warnings(caplog)
    assert matched, (
        f"no multi-client OAuth 2.1 warning emitted: {_warnings_from(caplog)}"
    )

    text = " ".join(matched)
    # The dead `default_key or "<none>"` was the tell that this branch had
    # never run. A message that still renders "<none>" has not been fixed,
    # only relocated.
    assert "<none>" not in text
    assert "default" in text.lower()


def test_oauth21_multi_client_warning_does_not_offer_the_disabled_tool_flow(
    monkeypatch, caplog, pinned_fastmcp_env
):
    # core/server.py's start_google_auth returns "disabled when OAuth 2.1 is
    # enabled" unconditionally, so directing an operator to the tool-level
    # flow is advice that provably cannot work.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    with caplog.at_level(logging.WARNING, logger="auth.oauth_config"):
        OAuthConfig()

    matched = _multi_client_warnings(caplog)
    assert matched, (
        f"no multi-client OAuth 2.1 warning emitted: {_warnings_from(caplog)}"
    )

    text = " ".join(matched)
    assert "personal" in text  # the client every login will actually use
    assert "contract" in text and "work" in text  # the ones that cannot be used
    # The remedy must be one that exists: turning OAuth 2.1 off, or running a
    # deployment per client. Naming the switch is what makes it actionable.
    assert "MCP_ENABLE_OAUTH21" in text
    assert "disabled" in text


def test_no_multi_client_warning_for_a_single_client_under_oauth21(
    monkeypatch, caplog, pinned_fastmcp_env
):
    # The warning describes a limitation that does not exist with one client;
    # emitting it there would train operators to ignore it.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "solo-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "solo-secret")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    with caplog.at_level(logging.WARNING, logger="auth.oauth_config"):
        OAuthConfig()

    assert _multi_client_warnings(caplog) == []


# --------------------------------------------------------------------------
# check_client_secrets: "is ANY client configured", not "is there a default"
# --------------------------------------------------------------------------


@pytest.fixture
def no_secrets_file(monkeypatch, tmp_path):
    """Point CONFIG_CLIENT_SECRETS_PATH at a path that does not exist."""
    absent = tmp_path / "client_secret.json"
    monkeypatch.setattr("auth.google_auth.CONFIG_CLIENT_SECRETS_PATH", str(absent))
    return absent


def test_check_client_secrets_accepts_a_registry_without_a_default(
    monkeypatch, no_secrets_file
):
    # This gates start_google_auth (core/server.py) and both callback
    # handlers. Asking only for the DEFAULT client made it answer "credentials
    # not found" for every user of a no-default registry -- including accounts
    # the registry maps perfectly well. The right question at this call site is
    # plural: is ANY OAuth client configured. Which client serves a given
    # account is resolve_oauth_client()'s decision, made later.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(NO_DEFAULT_DOC))

    assert check_client_secrets() is None


def test_check_client_secrets_accepts_a_registry_with_a_default(
    monkeypatch, no_secrets_file
):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))

    assert check_client_secrets() is None


def test_check_client_secrets_still_reports_a_wholly_unconfigured_server(
    no_secrets_file,
):
    message = check_client_secrets()

    assert message is not None
    assert str(no_secrets_file) in message


def test_check_client_secrets_remediation_names_every_working_source(no_secrets_file):
    # The old text advised setting GOOGLE_OAUTH_CLIENT_ID/SECRET. Whenever a
    # registry variable is set that advice is a guaranteed no-op, because
    # load_registry_from_env returns at the first source that hits and the
    # registry sources are checked first. The message must name the sources
    # that can actually take effect.
    message = check_client_secrets()

    assert message is not None
    assert "GOOGLE_OAUTH_CLIENTS_FILE" in message
    assert "GOOGLE_OAUTH_CLIENTS" in message
    assert "GOOGLE_OAUTH_CLIENT_ID" in message


def test_check_client_secrets_accepts_a_bare_secrets_file(monkeypatch, tmp_path):
    # No registry at all: the file fallback is the intended single-client path
    # and must keep working untouched.
    secrets = tmp_path / "client_secret.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "on-disk-id"}}))
    monkeypatch.setattr("auth.google_auth.CONFIG_CLIENT_SECRETS_PATH", str(secrets))

    assert check_client_secrets() is None


# --------------------------------------------------------------------------
# Refusal is fatal: an unresolvable account must never reach the file
# --------------------------------------------------------------------------


ON_DISK_SECRETS = {
    "installed": {
        "client_id": "on-disk-id",
        "client_secret": "on-disk-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


@pytest.fixture
def secrets_file_on_disk(monkeypatch, tmp_path):
    """A perfectly valid client_secret.json sitting where the fallback looks.

    Its presence is the whole point: the fail-open path was silent precisely
    because the file exists on a real deployment and the flow it produced
    looked fine right up until Google rejected it.
    """
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(ON_DISK_SECRETS))
    monkeypatch.setattr("auth.google_auth.CONFIG_CLIENT_SECRETS_PATH", str(path))
    return path


@pytest.fixture
def no_default_registry_config(monkeypatch):
    """Point auth.google_auth at a multi-client registry with no default."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(NO_DEFAULT_DOC))
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)
    return cfg


def test_unresolvable_account_raises_instead_of_returning_none(
    no_default_registry_config,
):
    # "The registry refused this account" and "the caller did not specify one"
    # shared the None sentinel, and every caller read it as the second.
    with pytest.raises(OAuthClientResolutionError) as excinfo:
        resolve_oauth_client(user_google_email="stranger@nowhere.test")

    message = str(excinfo.value)
    assert "stranger@nowhere.test" in message
    assert "personal" in message and "work" in message  # what IS registered


def test_resolution_with_no_account_and_no_default_raises(no_default_registry_config):
    # This is the callback's shape: a pre-multi-client state entry carries no
    # client_key and the callback has no email yet, so nothing selects a client.
    with pytest.raises(OAuthClientResolutionError):
        resolve_oauth_client()


def test_resolution_still_returns_none_when_nothing_is_configured(monkeypatch):
    # The surviving None has exactly one meaning: no registry at all, so the
    # caller's client-secrets-file fallback is the intended path.
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)

    assert resolve_oauth_client(user_google_email="anyone@anywhere.test") is None


def test_unresolvable_account_never_reaches_the_client_secrets_file(
    no_default_registry_config, secrets_file_on_disk
):
    with pytest.raises(OAuthClientResolutionError) as excinfo:
        create_oauth_flow(
            scopes=["https://www.googleapis.com/auth/userinfo.email"],
            redirect_uri="http://localhost:8000/oauth2callback",
            user_google_email="stranger@nowhere.test",
        )

    message = str(excinfo.value)
    # The error must name the real cause, not a missing file: the file is
    # right there, and blaming it sends the operator to fix the wrong thing.
    assert "stranger@nowhere.test" in message
    assert "not found" not in message
    assert str(secrets_file_on_disk) not in message


def test_callback_shaped_flow_with_no_client_key_never_reaches_the_file(
    no_default_registry_config, secrets_file_on_disk
):
    # The callback path had no guard at all. A state entry written before
    # multi-client support has client_key=None, so this is exactly the call
    # handle_auth_callback makes for one.
    with pytest.raises(OAuthClientResolutionError):
        create_oauth_flow(
            scopes=["https://www.googleapis.com/auth/userinfo.email"],
            redirect_uri="http://localhost:8000/oauth2callback",
            state="state-abc",
            code_verifier="verifier",
            autogenerate_code_verifier=False,
            client_key=None,
        )


def test_file_fallback_still_works_when_no_registry_is_configured(
    monkeypatch, secrets_file_on_disk
):
    # The fix must not break the single-client, file-only deployment.
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)

    flow = create_oauth_flow(
        scopes=["https://www.googleapis.com/auth/userinfo.email"],
        redirect_uri="http://localhost:8000/oauth2callback",
    )

    assert flow.client_config["client_id"] == "on-disk-id"


# --------------------------------------------------------------------------
# The seam: producer (start_auth_flow) joined to consumer (handle_auth_callback)
# --------------------------------------------------------------------------


REDIRECT_URI = "http://localhost:8000/oauth2callback"
SEAM_SCOPES = ["https://www.googleapis.com/auth/userinfo.email"]


class _StubCredentialStore:
    def __init__(self):
        self.saved = []

    def get_credential(self, user_email):  # noqa: ARG002
        return None

    def store_credential(self, user_email, credentials):
        self.saved.append((user_email, credentials))
        return True


def _auth_url_from(message: str) -> str:
    for line in message.splitlines():
        if "Authorization URL:" in line:
            return line.split("Authorization URL:", 1)[1].strip()
    raise AssertionError(f"no authorization URL in start_auth_flow output:\n{message}")


@pytest.fixture
def seam_harness(monkeypatch, tmp_path):
    """Everything except the two halves under test.

    The OAuth client registry, the state store and the Flow construction are
    all REAL -- mocking any of them would mock the seam itself. Only the
    Google boundary (fetch_token, userinfo) and the credential persistence are
    stubbed.
    """
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(THREE_CLIENT_DOC))
    config = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: config)

    state_file = tmp_path / "oauth_states.json"
    store = OAuth21SessionStore(oauth_state_file=str(state_file))
    monkeypatch.setattr("auth.google_auth.get_oauth21_session_store", lambda: store)

    built_client_ids = []

    class _RecordingFlow(Flow):
        """A real google-auth-oauthlib Flow that records how it was built."""

        @classmethod
        def from_client_config(cls, client_config, scopes, **kwargs):
            section = client_config.get("web") or client_config["installed"]
            built_client_ids.append(section["client_id"])
            return super().from_client_config(client_config, scopes, **kwargs)

        @classmethod
        def from_client_secrets_file(cls, client_secrets_file, scopes, **kwargs):
            raise AssertionError(
                f"flow fell back to the client secrets file at {client_secrets_file}"
            )

        def fetch_token(self, **kwargs):  # noqa: ARG002
            # The Google boundary. No network.
            return None

        @property
        def credentials(self):
            return Credentials(
                token="access-token",
                refresh_token="refresh-token",
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_config["client_id"],
                client_secret=self.client_config.get("client_secret") or None,
                scopes=list(SEAM_SCOPES),
            )

    monkeypatch.setattr("auth.google_auth.Flow", _RecordingFlow)
    monkeypatch.setattr(
        "auth.google_auth.get_current_scopes", lambda: list(SEAM_SCOPES)
    )
    monkeypatch.setattr(
        "auth.google_auth.get_transport_mode", lambda: "streamable-http"
    )
    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr(
        "auth.google_auth._determine_oauth_prompt",
        AsyncMock(return_value="consent"),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: _StubCredentialStore()
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session", lambda *args: None
    )

    return SimpleNamespace(
        config=config,
        store=store,
        state_file=state_file,
        built_client_ids=built_client_ids,
    )


@pytest.mark.asyncio
async def test_client_key_travels_from_start_auth_flow_to_the_callback_exchange(
    monkeypatch, seam_harness
):
    """Join the producer of client_key to its consumer.

    The resolver was covered in isolation and the state store was covered in
    isolation, and nothing joined them: deleting `client_key=oauth_client_key`
    from the store_oauth_state call, or `client_key=state_info.get("client_key")`
    from the callback's create_oauth_flow call, each left the FULL suite green.
    Both mutations fail here.

    analyst@example.com resolves through the 'example.com' domain mapping to
    the NON-default 'work' client, so a lost key shows up as the default
    'personal-id' rather than as an error.
    """
    monkeypatch.setattr(
        "auth.google_auth.get_user_info",
        lambda credentials: {"email": "analyst@example.com"},  # noqa: ARG005
    )

    message = await start_auth_flow(
        user_google_email="analyst@example.com",
        service_name="Gmail",
        redirect_uri=REDIRECT_URI,
    )

    # Producer: the authorization URL Google sees carries the work client.
    query = parse_qs(urlparse(_auth_url_from(message)).query)
    assert query["client_id"] == ["work-id"]
    state = query["state"][0]

    # The key is on the PERSISTED state, not just in memory.
    persisted = json.loads(seam_harness.state_file.read_text())
    assert persisted[state]["client_key"] == "work"

    # Consumer: the callback rebuilds the flow from that persisted state.
    user_email, credentials = await handle_auth_callback(
        scopes=list(SEAM_SCOPES),
        authorization_response=f"{REDIRECT_URI}?state={state}&code=fake-code",
        redirect_uri=REDIRECT_URI,
    )

    assert user_email == "analyst@example.com"
    # Two flows were built -- one per half -- and BOTH used the work client.
    # 'personal-id' here would mean the callback silently fell back to the
    # registry default, which is the failure this test exists to catch.
    assert seam_harness.built_client_ids == ["work-id", "work-id"]
    assert credentials.client_id == "work-id"


@pytest.mark.asyncio
async def test_start_auth_flow_refuses_an_account_the_registry_cannot_serve(
    monkeypatch, seam_harness, secrets_file_on_disk
):
    """Fail-closed at the caller, with nothing persisted.

    Nothing asserted that start_auth_flow treats an unresolvable account as an
    error rather than proceeding. A client_secret.json is deliberately present:
    the fail-open outcome was a perfectly ordinary-looking authorization URL
    built from it.
    """
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS", json.dumps(NO_DEFAULT_DOC))
    config = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: config)

    with pytest.raises(Exception) as excinfo:
        await start_auth_flow(
            user_google_email="stranger@nowhere.test",
            service_name="Gmail",
            redirect_uri=REDIRECT_URI,
        )

    message = str(excinfo.value)
    # The error must name the unresolvable account, not a missing file.
    assert "stranger@nowhere.test" in message
    assert str(secrets_file_on_disk) not in message
    # No flow was built and no state was persisted for a refused request.
    assert seam_harness.built_client_ids == []
    assert (
        not seam_harness.state_file.exists()
        or json.loads(seam_harness.state_file.read_text() or "{}") == {}
    )
