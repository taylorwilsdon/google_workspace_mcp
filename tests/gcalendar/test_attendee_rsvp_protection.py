"""Finding 13: manage_event must not let one person set another's RSVP.

`events.update`/`patch` replaces the whole attendee list, and `_normalize_attendees`
used to pass caller-supplied dicts through untouched. Anyone able to edit an event --
the organizer, or any guest when `guestsCanModify` is set -- could therefore write a
`responseStatus` for other attendees, accepting or declining on their behalf.
"""

import pytest

from gcalendar.calendar_tools import _normalize_attendees

SELF = "me@example.com"
OTHER = "victim@example.com"


class TestOtherAttendees:
    def test_response_status_for_another_attendee_is_dropped(self):
        result = _normalize_attendees(
            [{"email": OTHER, "responseStatus": "accepted"}], SELF
        )

        assert result == [{"email": OTHER}]

    def test_comment_for_another_attendee_is_dropped(self):
        """The comment travels with the RSVP, so it is the attendee's too."""
        result = _normalize_attendees(
            [{"email": OTHER, "responseStatus": "declined", "comment": "no"}], SELF
        )

        assert result == [{"email": OTHER}]

    def test_other_writable_fields_are_preserved(self):
        result = _normalize_attendees(
            [{"email": OTHER, "displayName": "V", "optional": True}], SELF
        )

        assert result == [{"email": OTHER, "displayName": "V", "optional": True}]

    def test_output_only_fields_are_not_echoed_back(self):
        result = _normalize_attendees(
            [{"email": OTHER, "self": True, "organizer": True, "id": "x"}], SELF
        )

        assert result == [{"email": OTHER}]


class TestOwnAttendee:
    def test_own_response_status_is_kept(self):
        result = _normalize_attendees(
            [{"email": SELF, "responseStatus": "accepted", "comment": "yes"}], SELF
        )

        assert result == [
            {"email": SELF, "responseStatus": "accepted", "comment": "yes"}
        ]

    def test_own_email_matching_is_case_insensitive(self):
        result = _normalize_attendees(
            [{"email": "Me@Example.COM", "responseStatus": "tentative"}], SELF
        )

        assert result == [{"email": "Me@Example.COM", "responseStatus": "tentative"}]


class TestMixedAndEdgeCases:
    def test_mixed_list_keeps_only_own_rsvp(self):
        result = _normalize_attendees(
            [
                SELF,
                {"email": SELF, "responseStatus": "accepted"},
                {"email": OTHER, "responseStatus": "accepted"},
                "third@example.com",
            ],
            SELF,
        )

        assert result == [
            {"email": SELF},
            {"email": SELF, "responseStatus": "accepted"},
            {"email": OTHER},
            {"email": "third@example.com"},
        ]

    def test_without_a_self_email_no_rsvp_is_accepted(self):
        """Fail closed: an unknown caller may not set anyone's RSVP."""
        result = _normalize_attendees(
            [{"email": SELF, "responseStatus": "accepted"}], None
        )

        assert result == [{"email": SELF}]

    @pytest.mark.parametrize("attendees", [None])
    def test_none_passes_through(self, attendees):
        assert _normalize_attendees(attendees, SELF) is None

    def test_plain_email_strings_are_unchanged(self):
        assert _normalize_attendees([SELF, OTHER], SELF) == [
            {"email": SELF},
            {"email": OTHER},
        ]

    def test_invalid_entries_are_skipped(self):
        assert _normalize_attendees([42, {"no_email": 1}, SELF], SELF) == [
            {"email": SELF}
        ]

    def test_empty_list_becomes_none(self):
        assert _normalize_attendees([], SELF) is None
