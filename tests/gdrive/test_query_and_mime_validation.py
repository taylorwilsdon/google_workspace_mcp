"""Findings 31 and 42: Drive query values and remote MIME types must be validated.

31: `list_docs_in_folder` interpolated `folder_id` into a Drive `q=` expression, so a
    quote could close the string literal and rewrite the filter -- dropping
    `trashed=false` or widening the search past the intended folder.
42: `create_drive_file` assigned the raw remote `Content-Type` to both `mime_type` and
    `file_metadata["mimeType"]`, letting the server being fetched from choose the
    stored MIME type, parameters and all.
"""

import pytest

from gdrive.drive_helpers import (
    escape_drive_query_value,
    normalize_remote_content_type,
    validate_drive_id,
)


class TestDriveIdValidation:
    @pytest.mark.parametrize(
        "value",
        [
            "1a2B3c-_dEfGhIjKlMnOpQrStUvWxYz",
            "root",
            "  root  ",  # surrounding whitespace is trimmed
            "0ABcDeFgHiJkLmNoPqRs",
        ],
    )
    def test_valid_ids_are_accepted(self, value):
        assert validate_drive_id(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            # The injection itself: closes the literal and appends query syntax.
            "x' in parents or '1' = '1",
            "abc' or name contains 'secret",
            "abc\\'",
            'abc"',
            "abc def",  # space
            "abc/def",
            "../etc",
            "",
            "   ",
        ],
    )
    def test_injection_shapes_are_rejected(self, value):
        with pytest.raises(ValueError):
            validate_drive_id(value)

    def test_error_names_the_field(self):
        with pytest.raises(ValueError, match="folder_id"):
            validate_drive_id("bad'id")

        with pytest.raises(ValueError, match="parent_id"):
            validate_drive_id("bad'id", field_name="parent_id")

    def test_non_string_is_rejected(self):
        with pytest.raises(ValueError):
            validate_drive_id(None)


class TestQueryValueEscaping:
    def test_quote_is_escaped(self):
        assert escape_drive_query_value("a'b") == "a\\'b"

    def test_backslash_is_escaped_first(self):
        r"""`\'` must become `\\\'`, not `\\'`.

        Escaping the quote first would turn a caller's backslash into the escape for
        the backslash we add, leaving the quote unescaped and terminating the literal.
        """
        assert escape_drive_query_value("a\\'b") == "a\\\\\\'b"

    def test_plain_text_is_unchanged(self):
        assert escape_drive_query_value("quarterly report") == "quarterly report"


class TestRemoteContentType:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("text/plain", "text/plain"),
            ("TEXT/PLAIN", "text/plain"),
            # Parameters are not part of a Drive mimeType.
            ("text/html; charset=utf-8", "text/html"),
            ("  application/pdf  ", "application/pdf"),
        ],
    )
    def test_usable_headers_are_normalised(self, header, expected):
        assert normalize_remote_content_type(header) == expected

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "",
            "   ",
            # The generic type carries no information, so keep the caller's choice.
            "application/octet-stream",
            # Malformed: cannot be a Drive mimeType.
            "not-a-mime-type",
            "text/plain/extra",
            "text/",
            "/plain",
            "text/pl ain",
            'text/"quoted"',
            # Google Apps types identify native documents, not uploadable bytes.
            "application/vnd.google-apps.document",
            "application/vnd.google-apps.folder",
        ],
    )
    def test_unusable_headers_fall_back_to_the_caller_choice(self, header):
        assert normalize_remote_content_type(header) is None
