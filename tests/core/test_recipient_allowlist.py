"""Tests for core/recipient_allowlist.py.

Covers the WORKSPACE_ALLOWED_RECIPIENTS semantics (absent → no-op, empty →
fail-closed, wildcard → allow all, list → exact case-insensitive match),
address extraction (Name <addr> forms, comma-separated strings, calendar
attendee dicts), the WORKSPACE_ALLOW_PUBLIC_SHARING gate, and the
manage_drive_access policy mapping.
"""

import pytest

from core.recipient_allowlist import (
    ALLOWLIST_ENV,
    PUBLIC_SHARING_ENV,
    RecipientNotAllowedError,
    allowlist_active,
    enforce_drive_access,
    enforce_event_attendees,
    enforce_public_sharing,
    enforce_recipients,
)


class TestAllowlistActive:
    def test_inactive_when_env_absent(self, monkeypatch):
        """Unset env var means the feature is off."""
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        assert allowlist_active() is False

    def test_active_when_env_set(self, monkeypatch):
        """Any value, even a list, engages enforcement."""
        monkeypatch.setenv(ALLOWLIST_ENV, "a@b.com")
        assert allowlist_active() is True

    def test_active_when_env_empty(self, monkeypatch):
        """An empty value still counts as opted in (fail-closed)."""
        monkeypatch.setenv(ALLOWLIST_ENV, "")
        assert allowlist_active() is True


class TestEnforceRecipients:
    def test_noop_when_env_absent(self, monkeypatch):
        """With no allowlist configured, any recipient passes."""
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        enforce_recipients(["anyone@example.com"], "send")

    def test_empty_env_blocks_all(self, monkeypatch):
        """An empty allowlist refuses every recipient."""
        monkeypatch.setenv(ALLOWLIST_ENV, "")
        with pytest.raises(RecipientNotAllowedError):
            enforce_recipients(["anyone@example.com"], "send")

    def test_wildcard_allows_any(self, monkeypatch):
        """The '*' wildcard lifts the restriction explicitly."""
        monkeypatch.setenv(ALLOWLIST_ENV, "*")
        enforce_recipients(["anyone@example.com"], "send")

    def test_listed_address_allowed(self, monkeypatch):
        """A listed address passes."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com, dad@example.com")
        enforce_recipients(["mum@example.com"], "send")

    def test_unlisted_address_refused(self, monkeypatch):
        """An unlisted address is refused, naming the address and operation."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        with pytest.raises(RecipientNotAllowedError) as exc:
            enforce_recipients(["stranger@example.com"], "send")
        # The refusal names the offending address and the operation so the
        # agent can relay it.
        assert "stranger@example.com" in str(exc.value)
        assert "send" in str(exc.value)

    def test_one_bad_recipient_blocks_whole_send(self, monkeypatch):
        """A single unlisted recipient blocks the whole operation."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        with pytest.raises(RecipientNotAllowedError):
            enforce_recipients(["mum@example.com", "stranger@example.com"], "send")

    def test_case_insensitive(self, monkeypatch):
        """Addresses match regardless of case."""
        monkeypatch.setenv(ALLOWLIST_ENV, "Mum@Example.COM")
        enforce_recipients(["mum@example.com"], "send")

    def test_name_addr_form(self, monkeypatch):
        """'Name <addr>' forms are reduced to the bare address."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_recipients(["Mum Person <mum@example.com>"], "send")

    def test_comma_separated_string_value(self, monkeypatch):
        """Comma-separated strings are split and each address checked."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com,dad@example.com")
        enforce_recipients(["mum@example.com, dad@example.com"], "send")
        with pytest.raises(RecipientNotAllowedError):
            enforce_recipients(["mum@example.com, stranger@example.com"], "send")

    def test_none_entries_skipped(self, monkeypatch):
        """None entries (e.g. an unset cc/bcc) are ignored."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_recipients(["mum@example.com", None, None], "send")

    def test_attendee_dicts(self, monkeypatch):
        """Calendar attendee dicts are checked by their 'email' key."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_recipients([{"email": "mum@example.com"}], "create_event")
        with pytest.raises(RecipientNotAllowedError):
            enforce_recipients([{"email": "stranger@example.com"}], "create_event")

    def test_no_recipients_is_allowed(self, monkeypatch):
        """An operation with no recipients is not outbound and always passes."""
        # e.g. a calendar event with no attendees while the list is empty
        monkeypatch.setenv(ALLOWLIST_ENV, "")
        enforce_recipients([], "create_event")
        enforce_recipients([None, None], "create_event")


class TestEnforcePublicSharing:
    def test_noop_when_allowlist_inactive(self, monkeypatch):
        """Public sharing is untouched when the allowlist is off."""
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        monkeypatch.delenv(PUBLIC_SHARING_ENV, raising=False)
        enforce_public_sharing("set_drive_file_permissions")

    def test_blocked_when_allowlist_active(self, monkeypatch):
        """Public sharing is blocked by default while the allowlist is active."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        monkeypatch.delenv(PUBLIC_SHARING_ENV, raising=False)
        with pytest.raises(RecipientNotAllowedError):
            enforce_public_sharing("set_drive_file_permissions")

    def test_explicit_override_allows(self, monkeypatch):
        """WORKSPACE_ALLOW_PUBLIC_SHARING=true permits public sharing."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        monkeypatch.setenv(PUBLIC_SHARING_ENV, "true")
        enforce_public_sharing("set_drive_file_permissions")

    def test_falsey_override_still_blocks(self, monkeypatch):
        """A falsey override value does not unlock public sharing."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        monkeypatch.setenv(PUBLIC_SHARING_ENV, "false")
        with pytest.raises(RecipientNotAllowedError):
            enforce_public_sharing("set_drive_file_permissions")


class TestEnforceDriveAccess:
    def test_noop_when_inactive(self, monkeypatch):
        """No Drive action is gated when the allowlist is off."""
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        enforce_drive_access("grant", share_type="anyone")
        enforce_drive_access("transfer_owner", new_owner_email="x@example.com")

    def test_grant_listed_user_allowed(self, monkeypatch):
        """A user grant to a listed address passes."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_drive_access("grant", share_type="user", share_with="mum@example.com")

    def test_grant_unlisted_group_refused(self, monkeypatch):
        """A group grant to an unlisted address is refused."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        with pytest.raises(RecipientNotAllowedError):
            enforce_drive_access(
                "grant", share_type="group", share_with="team@example.com"
            )

    def test_domain_or_anyone_grant_is_public_sharing(self, monkeypatch):
        """Domain and anyone grants fall under the public-sharing gate."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        monkeypatch.delenv(PUBLIC_SHARING_ENV, raising=False)
        for share_type in ("domain", "anyone"):
            with pytest.raises(RecipientNotAllowedError):
                enforce_drive_access("grant", share_type=share_type)
        monkeypatch.setenv(PUBLIC_SHARING_ENV, "true")
        enforce_drive_access("grant", share_type="anyone")

    def test_grant_batch_checks_every_entry(self, monkeypatch):
        """Every grant_batch entry is checked; domain entries count as public."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_drive_access("grant_batch", recipients=[{"email": "mum@example.com"}])
        with pytest.raises(RecipientNotAllowedError):
            enforce_drive_access(
                "grant_batch",
                recipients=[{"email": "mum@example.com"}, {"email": "x@example.com"}],
            )
        with pytest.raises(RecipientNotAllowedError):
            enforce_drive_access(
                "grant_batch",
                recipients=[{"share_type": "domain", "domain": "example.com"}],
            )

    def test_transfer_owner_unlisted_refused(self, monkeypatch):
        """Ownership transfer to an unlisted address is refused."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        with pytest.raises(RecipientNotAllowedError):
            enforce_drive_access("transfer_owner", new_owner_email="x@example.com")

    def test_update_and_revoke_never_gated(self, monkeypatch):
        """update and revoke act on existing grants and are never gated."""
        monkeypatch.setenv(ALLOWLIST_ENV, "")
        enforce_drive_access("update", share_with="x@example.com")
        enforce_drive_access("revoke", share_with="x@example.com")

    def test_grant_batch_routes_by_share_type(self, monkeypatch):
        """Batch entries route by their own share_type, not by email presence."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        monkeypatch.delenv(PUBLIC_SHARING_ENV, raising=False)
        enforce_drive_access(
            "grant_batch",
            recipients=[{"share_type": "group", "email": "mum@example.com"}],
        )
        # A listed email does not make an "anyone"/"domain" grant private.
        for entry in (
            {"share_type": "anyone", "email": "mum@example.com"},
            {"share_type": "domain", "domain": "example.com"},
        ):
            with pytest.raises(RecipientNotAllowedError):
                enforce_drive_access("grant_batch", recipients=[entry])


class TestEnforceEventAttendees:
    def test_skips_self_and_resources(self, monkeypatch):
        """The account's own entry and room resources are not third parties."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_event_attendees(
            [
                {"email": "me@example.com", "self": True},
                {"email": "room@resource.calendar.google.com", "resource": True},
                {"email": "mum@example.com"},
            ],
            "modify_event",
        )

    def test_unlisted_third_party_refused(self, monkeypatch):
        """An unlisted third-party attendee is refused."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        with pytest.raises(RecipientNotAllowedError, match="x@example.com"):
            enforce_event_attendees(
                [{"email": "me@example.com", "self": True}, {"email": "x@example.com"}],
                "modify_event",
            )

    def test_accepts_strings_and_none(self, monkeypatch):
        """Accepts None, plain strings and 'Name <addr>' forms."""
        monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
        enforce_event_attendees(None, "create_event")
        enforce_event_attendees(["Mum <mum@example.com>"], "create_event")
        with pytest.raises(RecipientNotAllowedError):
            enforce_event_attendees(["x@example.com"], "create_event")
