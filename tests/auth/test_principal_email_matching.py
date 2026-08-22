"""One definition of "same account" for every comparison on the auth path.

A review noted that the cross-account denial in `get_credentials` compared emails
case-sensitively while `assert_matches_principal` compared them case-insensitively.
The same pattern turned out to exist in five places, so rather than patching the one
that was reported, all of them were routed through `emails_match`.

Both spellings resolve to the same Google account, and every comparison here fails
closed, so the inconsistency was an availability bug rather than a bypass. It still
mattered: it emitted "SECURITY VIOLATION" for a benign spelling difference, which
devalues that log line as a signal.
"""

import pytest

from auth.principal import (
    PrincipalMismatchError,
    assert_matches_principal,
    emails_match,
    normalize_email,
)


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Owner@Example.com", "owner@example.com"),
            ("  owner@example.com  ", "owner@example.com"),
            ("OWNER@EXAMPLE.COM", "owner@example.com"),
            ("owner@example.com", "owner@example.com"),
            (None, ""),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalises(self, value, expected):
        assert normalize_email(value) == expected


class TestEmailsMatch:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("owner@example.com", "owner@example.com"),
            ("Owner@Example.com", "owner@example.com"),
            ("owner@example.com", "OWNER@EXAMPLE.COM"),
            (" owner@example.com ", "owner@example.com"),
        ],
    )
    def test_same_account(self, left, right):
        assert emails_match(left, right)
        assert emails_match(right, left), "must be symmetric"

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("owner@example.com", "attacker@example.com"),
            ("owner@example.com", "owner@evil.com"),
            # Distinct accounts must not collapse just because casing differs too.
            ("VICTIM@EXAMPLE.COM", "attacker@example.com"),
            # Google does not treat dots or +tags as equivalent for Workspace accounts,
            # so normalisation deliberately stops at case and whitespace.
            ("owner@example.com", "own.er@example.com"),
            ("owner@example.com", "owner+tag@example.com"),
        ],
    )
    def test_different_accounts(self, left, right):
        assert not emails_match(left, right)
        assert not emails_match(right, left), "must be symmetric"

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            (None, "owner@example.com"),
            ("owner@example.com", None),
            (None, None),
            ("", "owner@example.com"),
            ("owner@example.com", ""),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_empty_never_matches(self, left, right):
        """An absent identity must never satisfy a match, in either position."""
        assert not emails_match(left, right)


class TestAssertMatchesPrincipalUsesTheSamePredicate:
    def test_case_only_difference_is_accepted(self):
        assert_matches_principal(
            "Owner@Example.com", "owner@example.com", context="test"
        )

    def test_different_account_is_rejected(self):
        with pytest.raises(PrincipalMismatchError):
            assert_matches_principal(
                "attacker@example.com", "owner@example.com", context="test"
            )

    def test_absent_request_is_accepted(self):
        """No requested email means "act as the principal", not a mismatch."""
        assert_matches_principal(None, "owner@example.com", context="test")
        assert_matches_principal("", "owner@example.com", context="test")

    @pytest.mark.parametrize(
        "requested",
        ["Owner@Example.com", "owner@example.com", " OWNER@example.com "],
    )
    def test_agrees_with_emails_match(self, requested):
        """The two must never disagree: that disagreement was the reported bug."""
        principal = "owner@example.com"
        matched = emails_match(requested, principal)
        try:
            assert_matches_principal(requested, principal, context="test")
            raised = False
        except PrincipalMismatchError:
            raised = True
        assert matched is not raised
