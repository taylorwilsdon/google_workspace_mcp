"""Tests for the thread-ownership analysis helpers that back
`get_gmail_thread_content(include_analysis=True)`.

Covers the pure analyzer (_analyze_thread_ownership_impl), email
normalization, and date-header parsing against fabricated thread API
responses. No network, no credentials — the helpers are pure functions of
the Gmail threads.get(format='full') response shape.
"""

from __future__ import annotations

from typing import List

from gmail.gmail_helpers import (
    _analyze_thread_ownership_impl,
    _normalize_email,
    _parse_date_header,
)


def _msg(
    msg_id: str,
    from_addr: str,
    date: str,
    to: str = "",
    cc: str = "",
    subject: str = "Test",
    draft: bool = False,
    internal_date_ms: str | None = None,
) -> dict:
    """Build a fake Gmail message resource in the shape threads.get returns."""
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "Date", "value": date},
        {"name": "Subject", "value": subject},
    ]
    if to:
        headers.append({"name": "To", "value": to})
    if cc:
        headers.append({"name": "Cc", "value": cc})

    msg = {
        "id": msg_id,
        "labelIds": ["DRAFT"] if draft else ["INBOX"],
        "payload": {"headers": headers},
    }
    if internal_date_ms is not None:
        msg["internalDate"] = internal_date_ms
    return msg


def _thread(thread_id: str, messages: List[dict]) -> dict:
    return {"id": thread_id, "messages": messages}


class TestNormalization:
    def test_plain_email_lowercased(self):
        assert _normalize_email("Alex@Scopestack.io") == "alex@scopestack.io"

    def test_plus_addressing_stripped(self):
        assert _normalize_email("alex+foo@scopestack.io") == "alex@scopestack.io"

    def test_display_name_stripped(self):
        assert (
            _normalize_email("Alex Reynolds <alex@alexreynolds.com>")
            == "alex@alexreynolds.com"
        )

    def test_empty_or_malformed_returns_empty(self):
        assert _normalize_email("") == ""
        assert _normalize_email("not-an-email") == "not-an-email"


class TestDateParsing:
    def test_rfc822_header_parsed(self):
        iso, dt = _parse_date_header("Thu, 17 Apr 2026 10:00:00 -0400", None)
        assert iso is not None
        assert dt is not None

    def test_falls_back_to_internal_date(self):
        iso, dt = _parse_date_header("", "1713360000000")
        assert iso is not None
        assert dt is not None

    def test_both_missing_returns_none(self):
        iso, dt = _parse_date_header("", None)
        assert iso is None and dt is None

    def test_malformed_header_falls_back(self):
        iso, dt = _parse_date_header("not a date", "1713360000000")
        assert iso is not None
        assert dt is not None

    def test_internal_date_takes_precedence_over_header_date(self):
        iso, dt = _parse_date_header("Mon, 14 Apr 2026 10:00:00 -0400", "0")
        assert dt is not None
        assert dt.year == 1970
        assert iso == "1970-01-01T00:00:00+00:00"

    def test_aware_non_utc_date_normalized_to_utc(self):
        """REGRESSION: `last_timestamp` must always be UTC-aware per the
        documented contract. `parsedate_to_datetime` returns an aware
        datetime carrying the header's original offset (e.g., -0400). The
        previous implementation only coerced naive → UTC, so a well-formed
        non-UTC header would surface a non-UTC timestamp to callers."""
        iso, dt = _parse_date_header("Mon, 14 Apr 2026 10:00:00 -0400", None)
        assert dt is not None
        # Must be aware
        assert dt.tzinfo is not None
        # Must be UTC specifically — utcoffset() == 0
        assert dt.utcoffset().total_seconds() == 0
        # isoformat must end in a UTC marker ("+00:00"), not "-04:00"
        assert iso is not None
        assert iso.endswith("+00:00"), f"Expected UTC isoformat, got {iso!r}"


class TestBallInCourt:
    def test_vendor_replied_last_ball_on_alex(self):
        thread = _thread(
            "t1",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to="vendor@example.com",
                ),
                _msg(
                    "m2",
                    "Vendor <vendor@example.com>",
                    "Tue, 15 Apr 2026 10:00:00 -0400",
                    to="alex@alexreynolds.com",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "user"
        assert result["last_sender"] == "Vendor <vendor@example.com>"
        assert result["message_count"] == 2

    def test_alex_replied_last_ball_on_them(self):
        thread = _thread(
            "t2",
            [
                _msg(
                    "m1",
                    "Vendor <vendor@example.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
                _msg(
                    "m2",
                    "Alex <alex@alexreynolds.com>",
                    "Tue, 15 Apr 2026 10:00:00 -0400",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "them"
        assert result["last_sender"] == "Alex <alex@alexreynolds.com>"

    def test_outbound_only_external_thread_ball_on_them(self):
        thread = _thread(
            "t-outbound",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to="Vendor <vendor@example.com>",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "them"
        assert result["participants"] == [
            "alex@alexreynolds.com",
            "vendor@example.com",
        ]

    def test_plus_addressing_recognized_as_user(self):
        """alex+foo@alexreynolds.com is still Alex."""
        thread = _thread(
            "t3",
            [
                _msg(
                    "m1",
                    "Vendor <vendor@example.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
                _msg(
                    "m2",
                    "Alex <alex+newsletter@alexreynolds.com>",
                    "Tue, 15 Apr 2026 10:00:00 -0400",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "them"


class TestDrafts:
    def test_draft_excluded_from_last_determination(self):
        """A newer DRAFT from Alex must NOT flip ball-in-court; drafts
        haven't been sent."""
        thread = _thread(
            "t4",
            [
                _msg(
                    "m1",
                    "Vendor <vendor@example.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
                _msg(
                    "m2",
                    "Alex <alex@alexreynolds.com>",
                    "Tue, 15 Apr 2026 10:00:00 -0400",
                    draft=True,
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "user"  # Vendor is still last sent
        assert result["excluded_drafts"] == 1
        assert result["last_sender"] == "Vendor <vendor@example.com>"

    def test_all_drafts_returns_none_ball(self):
        thread = _thread(
            "t5",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    draft=True,
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] is None
        assert result["excluded_drafts"] == 1


class TestParticipantsAndCounts:
    def test_three_party_thread(self):
        thread = _thread(
            "t6",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to="vendor@example.com",
                    cc="colleague@example.com",
                ),
                _msg(
                    "m2",
                    "Vendor <vendor@example.com>",
                    "Mon, 14 Apr 2026 11:00:00 -0400",
                    to="alex@alexreynolds.com",
                ),
                _msg(
                    "m3",
                    "Colleague <colleague@example.com>",
                    "Tue, 15 Apr 2026 08:00:00 -0400",
                    to="alex@alexreynolds.com",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert set(result["participants"]) == {
            "alex@alexreynolds.com",
            "vendor@example.com",
            "colleague@example.com",
        }
        assert result["message_count_by_sender"]["alex@alexreynolds.com"] == 1
        assert result["message_count_by_sender"]["vendor@example.com"] == 1
        assert result["message_count_by_sender"]["colleague@example.com"] == 1

    def test_quoted_display_name_with_comma_parses_correctly(self):
        """REGRESSION: a naive split(',') mis-parses To/Cc headers like
          "Doe, John" <john@example.com>, vendor@example.com
        The quoted comma splits the first recipient into two pieces and
        the participant set picks up a phantom "John" entry.
        email.utils.getaddresses is the RFC-correct parser; this test
        locks that in."""
        thread = _thread(
            "t-quoted-comma",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to='"Doe, John" <john@example.com>, vendor@example.com',
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        # Exactly three real participants; no phantom entries from
        # mis-splitting a quoted display name.
        assert set(result["participants"]) == {
            "alex@alexreynolds.com",
            "john@example.com",
            "vendor@example.com",
        }

    def test_user_forwards_to_self_returns_none(self):
        """User → User (forward-to-self or archive-to-self): there's no
        external party to owe anything, so ball_in_court_of is None rather
        than 'them'. Downstream agents doing 'find threads where ball is on
        them' shouldn't match self-only threads."""
        thread = _thread(
            "t7",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to="alex+archive@alexreynolds.com",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] is None


class TestEmptyAndMalformed:
    def test_empty_thread(self):
        result = _analyze_thread_ownership_impl(
            {"id": "empty", "messages": []}, "alex@alexreynolds.com"
        )
        assert result["message_count"] == 0
        assert result["ball_in_court_of"] is None
        assert result["last_sender"] is None

    def test_malformed_date_falls_back_to_internal(self):
        """Thread with bad Date header still computes last-sender using internalDate."""
        thread = _thread(
            "t8",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "bad-date-value",
                    internal_date_ms="1713360000000",
                ),
                _msg(
                    "m2",
                    "Vendor <vendor@example.com>",
                    "bad-date-value",
                    internal_date_ms="1713450000000",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "user"
        assert result["last_sender"] == "Vendor <vendor@example.com>"

    def test_internal_date_controls_last_message_when_header_date_skewed(self):
        thread = _thread(
            "t-skew",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Wed, 15 Apr 2026 09:00:00 -0400",
                    to="vendor@example.com",
                    internal_date_ms="1776160800000",
                ),
                _msg(
                    "m2",
                    "Vendor <vendor@example.com>",
                    "Tue, 14 Apr 2026 09:00:00 -0400",
                    to="alex@alexreynolds.com",
                    internal_date_ms="1776247200000",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "user"
        assert result["last_sender"] == "Vendor <vendor@example.com>"

    def test_naive_and_aware_datetimes_do_not_raise(self):
        """REGRESSION: parsedate_to_datetime returns a naive datetime when
        the header has no timezone (e.g., 'Mon, 14 Apr 2026 09:00:00'). The
        internalDate fallback path returns aware UTC. Mixing them in > / <
        comparisons raises TypeError. _parse_date_header must coerce naive
        to UTC so both paths return comparable datetimes."""
        thread = _thread(
            "t-tz",
            [
                # No timezone on Date header → naive after parsing
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00",
                    to="vendor@example.com",
                ),
                # Malformed Date → falls back to internalDate (aware UTC).
                # Pick a timestamp after m1's 2026-04-14 09:00 so the
                # chronological comparison resolves m2 as the last message.
                _msg(
                    "m2",
                    "Vendor <vendor@example.com>",
                    "not-a-valid-date",
                    to="alex@alexreynolds.com",
                    internal_date_ms="1776600000000",
                ),  # ~Apr 2026
            ],
        )
        # If regression, this raises TypeError from > comparison
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] == "user"
        assert result["last_sender"] == "Vendor <vendor@example.com>"

    def test_malformed_from_header_returns_none_ball(self):
        """If the last message's From header has no parseable address (just
        a display name, or garbage), we can't determine ball-in-court.
        Return None rather than silently defaulting to 'user'."""
        thread = _thread(
            "t-bad-from",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
                # Second message has no parseable email in From
                _msg("m2", "No Email Here", "Tue, 15 Apr 2026 09:00:00 -0400"),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["ball_in_court_of"] is None

    def test_single_message_thread(self):
        thread = _thread(
            "t9",
            [
                _msg(
                    "m1",
                    "Vendor <vendor@example.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        assert result["message_count"] == 1
        assert result["ball_in_court_of"] == "user"
        assert result["last_sender"] == "Vendor <vendor@example.com>"

    def test_draft_recipients_do_not_leak_into_ball_in_court(self):
        """REGRESSION: participants.add runs BEFORE the is_draft skip, so
        draft-only recipients pollute the participants set. If the
        external-party check uses `participants`, a user → self non-draft
        plus a user → external DRAFT flips ball_in_court_of from None to
        'them', violating the documented self-only-thread contract. The fix:
        compute external-party presence from non-draft participants, not the
        public participants list."""
        thread = _thread(
            "t-draft-leak",
            [
                # Non-draft: user → self (archival / forward-to-self)
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                    to="alex+archive@alexreynolds.com",
                ),
                # Draft naming an external recipient, never sent
                _msg(
                    "m2",
                    "Alex <alex@alexreynolds.com>",
                    "Tue, 15 Apr 2026 09:00:00 -0400",
                    to="vendor@example.com",
                    draft=True,
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")
        # No external party has SENT anything, so this is a self-only
        # thread for ball-in-court purposes. Must return None.
        assert result["ball_in_court_of"] is None
        assert result["excluded_drafts"] == 1
        # The draft recipient IS still in `participants` — that's the
        # documented contract (full address set including drafts).
        assert "vendor@example.com" in result["participants"]

    def test_malformed_headers_do_not_leak_into_participants_or_counts(self):
        """REGRESSION: `_normalize_email` intentionally returns its input
        unchanged when there's no '@', so a From header like 'No Email Here'
        previously leaked 'no email here' into both the participants list
        and message_count_by_sender. Both counters must gate on '@' presence
        so downstream agents never see free-text tokens as if they were
        email addresses."""
        thread = _thread(
            "t-malformed-leak",
            [
                _msg(
                    "m1",
                    "Alex <alex@alexreynolds.com>",
                    "Mon, 14 Apr 2026 09:00:00 -0400",
                ),
                # From is just a display name with no address. To is also
                # just text — both should be filtered out by the @-guard.
                _msg(
                    "m2",
                    "No Email Here",
                    "Tue, 15 Apr 2026 09:00:00 -0400",
                    to="Just A Name",
                ),
            ],
        )
        result = _analyze_thread_ownership_impl(thread, "alex@alexreynolds.com")

        # Every participant must be a real address (contains "@")
        for p in result["participants"]:
            assert "@" in p, f"Malformed token leaked into participants: {p!r}"
        # Every sender key must be a real address (contains "@")
        for sender in result["message_count_by_sender"]:
            assert "@" in sender, (
                f"Malformed token leaked into message_count_by_sender: {sender!r}"
            )
