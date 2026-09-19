import auth.account_identity as account_identity


class _CredentialStore:
    def __init__(self, users):
        self.users = list(users)
        self.list_calls = 0

    def list_users(self):
        self.list_calls += 1
        return self.users


def test_single_user_uses_sole_stored_account_as_default(monkeypatch):
    store = _CredentialStore(["user@example.com"])
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.single_user is True
    assert identity.stored_users == ("user@example.com",)
    assert identity.sole_stored_email == "user@example.com"
    assert identity.default_email == "user@example.com"
    assert identity.default_is_sole_stored is True


def test_configured_account_wins_over_sole_stored_account(monkeypatch):
    store = _CredentialStore(["stored@example.com"])
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.default_email == "configured@example.com"
    assert identity.default_is_sole_stored is False


def test_configured_account_uses_stored_casing_when_it_matches(monkeypatch):
    store = _CredentialStore(["User@Example.com"])
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "user@example.com")
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.configured_email == "User@Example.com"
    assert identity.default_email == "User@Example.com"


def test_multiple_stored_accounts_do_not_create_implicit_default(monkeypatch):
    store = _CredentialStore(["a@example.com", "b@example.com"])
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.default_email is None
    assert identity.sole_stored_email is None


def test_non_single_user_mode_does_not_enumerate_credentials(monkeypatch):
    store = _CredentialStore(["user@example.com"])
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.single_user is False
    assert identity.stored_users == ()
    assert identity.default_email is None
    assert store.list_calls == 0


def test_canonical_stored_email_is_case_insensitive(monkeypatch):
    store = _CredentialStore(["User@Example.com"])
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(account_identity, "get_credential_store", lambda: store)

    identity = account_identity.get_legacy_account_identity()

    assert identity.canonical_stored_email("user@example.com") == "User@Example.com"
    assert identity.canonical_stored_email("other@example.com") is None
