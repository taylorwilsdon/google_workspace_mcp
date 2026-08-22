"""Findings 21/22/35-37: `get_credentials` must not serve another account's grant.

The file-backed credential store is keyed by email and holds every user who has
ever authorised this server. A request whose MCP session is bound to one account
therefore must not be able to reach a different account's entry -- previously a
session/email mismatch only *skipped the session cache* and then read the
requested email straight out of the credential store.
"""

from auth.google_auth import get_credentials


class _ValidCredentials:
    def __init__(self, token="victim-token"):
        self.token = token
        self.refresh_token = "refresh-token"
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.scopes = ["scope.a"]
        self.expiry = None
        self.valid = True
        self.expired = False

    def refresh(self, request):  # noqa: ARG002
        raise AssertionError("refresh must not be attempted for a denied request")


class _BoundSessionStore:
    """Session store whose MCP session is bound to `session_user`."""

    def __init__(self, session_user):
        self._session_user = session_user
        self.credentials_by_session_calls = 0

    def get_user_by_mcp_session(self, session_id):  # noqa: ARG002
        return self._session_user

    def get_credentials_by_mcp_session(self, session_id):  # noqa: ARG002
        self.credentials_by_session_calls += 1
        return None

    def store_session(self, **kwargs):  # noqa: ARG002
        raise AssertionError("store_session must not run for a denied request")


class _RecordingCredentialStore:
    def __init__(self, credentials):
        self._credentials = credentials
        self.get_calls = []

    def get_credential(self, user_email):
        self.get_calls.append(user_email)
        return self._credentials

    def store_credential(self, user_email, credentials):  # noqa: ARG002
        raise AssertionError("store_credential must not run for a denied request")


def _patch_common(monkeypatch, oauth_store, credential_store):
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )


def test_bound_session_cannot_read_another_users_stored_credentials(monkeypatch):
    victim_credentials = _ValidCredentials()
    oauth_store = _BoundSessionStore(session_user="attacker@example.com")
    credential_store = _RecordingCredentialStore(victim_credentials)
    _patch_common(monkeypatch, oauth_store, credential_store)

    result = get_credentials(
        user_google_email="victim@example.com",
        required_scopes=["scope.a"],
        session_id="session-attacker",
    )

    assert result is None
    # The denial happens before any store lookup, so the victim's entry is never read.
    assert credential_store.get_calls == []
    assert oauth_store.credentials_by_session_calls == 0


def test_matching_session_still_resolves_from_credential_store(monkeypatch):
    """The denial must be scoped to mismatches, not break the normal path."""
    own_credentials = _ValidCredentials(token="own-token")
    oauth_store = _BoundSessionStore(session_user="owner@example.com")
    credential_store = _RecordingCredentialStore(own_credentials)
    _patch_common(monkeypatch, oauth_store, credential_store)
    monkeypatch.setattr(
        "auth.google_auth.load_credentials_from_session", lambda session_id: None
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda session_id, credentials: None,
    )

    result = get_credentials(
        user_google_email="owner@example.com",
        required_scopes=["scope.a"],
        session_id="session-owner",
    )

    assert result is own_credentials
    assert credential_store.get_calls == ["owner@example.com"]


def test_unbound_session_still_resolves_requested_user(monkeypatch):
    """No binding yet (first call of a session) must not be treated as a mismatch."""
    own_credentials = _ValidCredentials(token="own-token")
    oauth_store = _BoundSessionStore(session_user=None)
    credential_store = _RecordingCredentialStore(own_credentials)
    _patch_common(monkeypatch, oauth_store, credential_store)
    monkeypatch.setattr(
        "auth.google_auth.load_credentials_from_session", lambda session_id: None
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda session_id, credentials: None,
    )

    result = get_credentials(
        user_google_email="owner@example.com",
        required_scopes=["scope.a"],
        session_id="session-new",
    )

    assert result is own_credentials
    assert credential_store.get_calls == ["owner@example.com"]


def test_case_only_difference_is_not_treated_as_a_cross_account_request(monkeypatch):
    """A case-only difference names the same account, so it must not be denied.

    The mismatch check fails closed, so this was an availability bug rather than a
    bypass: it logged a cross-account denial for a benign spelling difference, which
    both blocked the caller and made the denial log untrustworthy as a signal. All five
    comparisons on the auth path now share `emails_match`, so they cannot disagree with
    `assert_matches_principal` about whether two spellings are one account.

    Note the credential store itself is still keyed by the exact string, so a lookup
    with different casing than the stored key can still miss. That is a cache miss, not
    a security decision.
    """
    own_credentials = _ValidCredentials(token="own-token")
    oauth_store = _BoundSessionStore(session_user="owner@example.com")
    credential_store = _RecordingCredentialStore(own_credentials)
    _patch_common(monkeypatch, oauth_store, credential_store)
    monkeypatch.setattr(
        "auth.google_auth.load_credentials_from_session", lambda session_id: None
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda session_id, credentials: None,
    )

    result = get_credentials(
        user_google_email="Owner@Example.COM",
        required_scopes=["scope.a"],
        session_id="session-owner",
    )

    assert result is own_credentials


def test_different_account_is_still_denied_when_casing_also_differs(monkeypatch):
    """Normalising for comparison must not collapse genuinely different accounts."""
    victim_credentials = _ValidCredentials()
    oauth_store = _BoundSessionStore(session_user="attacker@example.com")
    credential_store = _RecordingCredentialStore(victim_credentials)
    _patch_common(monkeypatch, oauth_store, credential_store)

    result = get_credentials(
        user_google_email="VICTIM@EXAMPLE.COM",
        required_scopes=["scope.a"],
        session_id="session-attacker",
    )

    assert result is None
    assert credential_store.get_calls == []
