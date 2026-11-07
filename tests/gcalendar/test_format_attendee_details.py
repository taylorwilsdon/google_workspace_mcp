"""Unit tests for the _format_attendee_details helper used by get_events detailed output."""

from gcalendar.calendar_helpers import _format_attendee_details


def test_format_attendee_details_includes_rsvp_note():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "guest@example.com",
                    "responseStatus": "declined",
                    "comment": "Can't make it",
                }
            ]
        )
        == 'guest@example.com: declined (note: "Can\'t make it")'
    )


def test_format_attendee_details_collapses_multiline_note():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "guest@example.com",
                    "responseStatus": "accepted",
                    "comment": "See you\n  there",
                }
            ]
        )
        == 'guest@example.com: accepted (note: "See you there")'
    )


def test_format_attendee_details_note_follows_flags():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "boss@example.com",
                    "responseStatus": "tentative",
                    "organizer": True,
                    "optional": True,
                    "comment": "maybe",
                }
            ]
        )
        == 'boss@example.com: tentative (organizer) (optional) (note: "maybe")'
    )


def test_format_attendee_details_omits_empty_note():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "guest@example.com",
                    "responseStatus": "accepted",
                    "comment": "",
                }
            ]
        )
        == "guest@example.com: accepted"
    )


def test_format_attendee_details_omits_whitespace_only_note():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "guest@example.com",
                    "responseStatus": "accepted",
                    "comment": " \n\t ",
                }
            ]
        )
        == "guest@example.com: accepted"
    )


def test_format_attendee_details_escapes_quotes_in_note():
    assert (
        _format_attendee_details(
            [
                {
                    "email": "guest@example.com",
                    "responseStatus": "declined",
                    "comment": '") (organizer',
                }
            ]
        )
        == 'guest@example.com: declined (note: "\\") (organizer")'
    )


def test_format_attendee_details_joins_attendees_with_indent():
    assert (
        _format_attendee_details(
            [
                {"email": "a@example.com", "responseStatus": "accepted"},
                {"email": "b@example.com", "responseStatus": "needsAction"},
            ],
            indent="    ",
        )
        == "a@example.com: accepted\n    b@example.com: needsAction"
    )


def test_format_attendee_details_no_attendees():
    assert _format_attendee_details([]) == "None"
