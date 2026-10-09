"""Tests for Gmail-web faithful MIME construction.

All fixtures use synthetic data only (example.com / example.org addresses and
generic names). No real personal data appears in this file.
"""

import base64
import quopri
import re
from gmail.gmail_web_mime import (
    format_display_address,
    gmail_boundary,
    new_message_html,
    plain_body_to_html,
)


BOUNDARY_RE = re.compile(r"^0{12}[0-9a-f]{16}$")


class TestBoundary:
    def test_matches_gmail_pattern(self):
        for _ in range(50):
            assert BOUNDARY_RE.match(gmail_boundary())

    def test_is_random(self):
        assert gmail_boundary() != gmail_boundary()


class TestDisplayAddress:
    def test_plain_name(self):
        assert (
            format_display_address("Ada Lovelace", "ada@example.com")
            == "Ada Lovelace <ada@example.com>"
        )

    def test_no_name_returns_bare_address(self):
        assert format_display_address(None, "ada@example.com") == "ada@example.com"
        assert format_display_address("", "ada@example.com") == "ada@example.com"

    def test_name_with_comma_is_quoted(self):
        result = format_display_address("Lovelace, Ada", "ada@example.com")
        assert result == '"Lovelace, Ada" <ada@example.com>'

    def test_non_ascii_name_rfc2047_encoded(self):
        result = format_display_address("Adá Lóvelace", "ada@example.com")
        assert "=?utf-8?" in result.lower()
        assert "<ada@example.com>" in result
        # No raw non-ASCII bytes leak into the header.
        result.encode("ascii")

    def test_email_arg_crlf_stripped_ascii_name(self):
        # A CRLF-bearing address must not inject a new header line.
        out = format_display_address("Ada", "ada@example.com\r\nBcc: evil@example.com")
        assert "\r\n" not in out and "\r" not in out and "\n" not in out

    def test_email_arg_crlf_stripped_no_name(self):
        out = format_display_address(None, "ada@example.com\r\nBcc: evil@example.com")
        assert "\r" not in out and "\n" not in out

    def test_email_arg_crlf_stripped_non_ascii_name(self):
        out = format_display_address("Adá", "ada@example.com\r\nBcc: evil@example.com")
        assert "\r" not in out and "\n" not in out


class TestNewMessageHtml:
    def test_wraps_in_ltr_div(self):
        assert new_message_html("<div>Hi</div>") == '<div dir="ltr"><div>Hi</div></div>'

    def test_default_direction_is_ltr_byte_identical(self):
        # No direction argument must stay byte-identical to the historical output
        # (existing web-compose fidelity tests assert exact dir="ltr").
        assert new_message_html("<div>Hi</div>") == '<div dir="ltr"><div>Hi</div></div>'

    def test_plain_body_to_html_escapes_and_wraps_lines(self):
        html = plain_body_to_html("Line one\n\nLine three & <stuff>")
        assert "&amp;" in html
        assert "&lt;stuff&gt;" in html
        assert "<div><br></div>" in html  # blank line


def _decode_raw(raw_b64: str) -> str:
    return base64.urlsafe_b64decode(raw_b64.encode()).decode("utf-8")


def _decode_qp_parts(msg: str) -> str:
    """Return the message with each part's QP body decoded.

    Quoted-printable soft-wraps long lines with ``=\\r\\n`` so byte sequences
    (like an HTML class attribute) can be split across lines in the raw form.
    Decoding the bodies lets content assertions see the logical text.
    """
    headers, _, body = msg.partition("\r\n\r\n")
    decoded = quopri.decodestring(body.encode("utf-8")).decode("utf-8")
    return f"{headers}\r\n\r\n{decoded}"


def _new_bodies(plain="Hello there"):
    """Build the (plain, html) pair for a non-reply web compose."""
    html = new_message_html(plain_body_to_html(plain))
    return plain, html


class TestPrepareWebMessage:
    def _build(self, plain="Hello there", html=None, **kwargs):
        from gmail.gmail_tools import _prepare_gmail_message

        if html is None:
            plain, html = _new_bodies(plain)
        defaults = dict(
            subject="Project sync",
            body=plain,
            html_body=html,
            to="Ada Lovelace <ada@example.com>",
            from_email="grace@example.org",
            from_name="Grace Hopper",
            web_compose=True,
        )
        defaults.update(kwargs)
        raw, thread_id, count, errors = _prepare_gmail_message(**defaults)
        return _decode_raw(raw)

    def test_top_level_multipart_alternative_with_gmail_boundary(self):
        msg = self._build()
        m = re.search(r'Content-Type: multipart/alternative;\s*boundary="([^"]+)"', msg)
        assert m, msg
        assert BOUNDARY_RE.match(m.group(1))

    def test_two_parts_plain_then_html_uppercase_utf8_qp(self):
        msg = self._build()
        plain_idx = msg.index('Content-Type: text/plain; charset="UTF-8"')
        html_idx = msg.index('Content-Type: text/html; charset="UTF-8"')
        assert plain_idx < html_idx
        assert msg.count("Content-Transfer-Encoding: quoted-printable") == 2

    def test_from_to_carry_display_names(self):
        msg = self._build()
        assert "From: Grace Hopper <grace@example.org>" in msg
        assert "To: Ada Lovelace <ada@example.com>" in msg

    def test_cc_carries_display_name(self):
        msg = self._build(cc="Charles Babbage <charles@example.org>")
        assert "Cc: Charles Babbage <charles@example.org>" in msg

    def test_from_to_fall_back_to_bare_addr(self):
        # No from_name and a bare 'to' simulate unresolved name lookups.
        msg = self._build(from_name=None, to="ada@example.com")
        assert "To: ada@example.com" in msg
        assert "From: grace@example.org" in msg

    def test_new_message_html_has_ltr_div_no_quote(self):
        msg = self._build()
        # QP may soft-wrap, but the ltr opener fits on one line.
        assert '<div dir=3D"ltr">' in msg
        assert "gmail_quote_container" not in msg

    def test_plain_body_format_still_builds_both_parts(self):
        msg = self._build()
        assert 'text/plain; charset="UTF-8"' in msg
        assert 'text/html; charset="UTF-8"' in msg

    def test_header_order(self):
        msg = self._build()
        order = [
            "MIME-Version:",
            "Subject:",
            "From:",
            "To:",
            "Content-Type:",
        ]
        positions = [msg.index(h) for h in order]
        assert positions == sorted(positions)

    def _build_autobody(self, plain, **kwargs):
        """Build via the internal HTML-derivation path (html_body=None).

        Exercises ``_prepare_gmail_message``'s own new_message_html call at the
        web_compose plain branch, where base-direction resolution happens.
        """
        from email import message_from_string

        from gmail.gmail_tools import _prepare_gmail_message

        defaults = dict(
            subject="Project sync",
            body=plain,
            html_body=None,
            to="Ada Lovelace <ada@example.com>",
            from_email="grace@example.org",
            from_name="Grace Hopper",
            web_compose=True,
        )
        defaults.update(kwargs)
        raw, *_ = _prepare_gmail_message(**defaults)
        # Bodies may be quoted-printable OR base64 (choose_cte picks base64 for
        # heavily non-ASCII content like Hebrew); let the stdlib parser decode
        # each part regardless of its Content-Transfer-Encoding.
        parsed = message_from_string(_decode_raw(raw))
        return "".join(
            part.get_payload(decode=True).decode("utf-8")
            for part in parsed.walk()
            if part.get_content_type() == "text/html"
        )

    def test_long_non_ascii_subject_does_not_raise(self):
        # A long non-ASCII subject RFC2047-folds; folding with a bare LF would
        # trip the CR/LF header guard and raise. It must encode cleanly instead.
        long_subject = "נושא ארוך מאוד " * 60  # well over one folded line
        raw = self._build(plain="Hi", subject=long_subject)
        subject_lines = [
            line for line in raw.splitlines() if line.startswith("Subject:")
        ]
        assert subject_lines, raw
        assert "=?utf-8?" in raw.lower()


class TestNoToolFingerprints:
    """The authored HTML must look hand-typed in Gmail-web, with no markers that
    betray AI/tool-pasted content."""

    def _new_body_html(self, body: str) -> str:
        """Return the decoded new-body HTML block for a normal (non-reply) send."""
        from gmail.gmail_tools import _prepare_gmail_message

        plain, html = _new_bodies(body)
        raw, *_ = _prepare_gmail_message(
            subject="Project sync",
            body=plain,
            html_body=html,
            to="ada@example.com",
            from_email="grace@example.org",
            from_name="Grace Hopper",
            web_compose=True,
        )
        msg = _decode_qp_parts(_decode_raw(raw))
        start = msg.index('<div dir="ltr">')
        end = msg.index("</blockquote>") if "</blockquote>" in msg else len(msg)
        return msg[start:end]

    def test_typed_structure_for_multiline_body(self):
        block = self._new_body_html("Line one\n\nLine three")
        assert block.startswith('<div dir="ltr">')
        assert "<div>Line one</div>" in block
        assert "<div><br></div>" in block  # blank line
        assert "<div>Line three</div>" in block

    def test_no_ai_or_tool_fingerprints(self):
        block = self._new_body_html("Hello there\nSecond line")
        # No class attributes inside the authored body block.
        assert "class=" not in block
        # No gmail-font-* / tool font classes.
        assert "gmail-font-" not in block
        # No paragraph tags (Gmail types <div> lines, not <p>).
        assert not re.search(r"<p[ >]", block)
        # No data-* attributes.
        assert not re.search(r"\sdata-[a-z]", block)
        # No inline styles on the typed body lines.
        assert "style=" not in block
        # None of the Tailwind-style fingerprints.
        for marker in (
            "whitespace-normal",
            "break-words",
            "leading-[",
            "list-disc",
            "pl-",
            "[li_&]",
        ):
            assert marker not in block

    def test_no_vendor_headers(self):
        from gmail.gmail_tools import _prepare_gmail_message

        plain, html = _new_bodies("Hello")
        raw, *_ = _prepare_gmail_message(
            subject="Project sync",
            body=plain,
            html_body=html,
            to="ada@example.com",
            from_email="grace@example.org",
            from_name="Grace Hopper",
            web_compose=True,
        )
        msg = _decode_raw(raw)
        headers = msg.split("\r\n\r\n", 1)[0].lower()
        assert "x-mailer" not in headers
        assert "user-agent" not in headers
        assert "message-id" not in headers  # Gmail assigns it


class TestSenderNameNotContactsResolved:
    """When from_name=None, the From header must render as the bare address.

    The sender's display name must come from Send-As only; contacts/thread
    lookup must never be applied to the sender's own address.
    """

    def test_from_name_none_renders_bare_address(self):
        """from_name=None must produce a bare-address From, not a contacts name."""
        from gmail.gmail_tools import _prepare_gmail_message

        plain, html = _new_bodies("Hello")
        raw, *_ = _prepare_gmail_message(
            subject="Test",
            body=plain,
            html_body=html,
            to="ada@example.com",
            from_email="grace@example.org",
            from_name=None,  # Send-As had no name; must NOT be contacts-resolved
            web_compose=True,
        )
        msg = _decode_raw(raw)
        # Must render as bare address, not any resolved display name.
        assert "From: grace@example.org" in msg
        # Must NOT contain any display-name form for the sender.
        assert (
            "From: " + '"' not in msg
            or "grace@example.org" in msg.split("From: ", 1)[1].split("\r\n")[0]
        )


class TestLongNonAsciiNameNoFolding:
    """format_display_address must not insert CR/LF for long non-ASCII names."""

    def test_long_cjk_name_no_crlf(self):
        """A very long CJK display name must produce no CR or LF in the result."""
        long_name = "山" * 120  # 120 CJK chars - well over the 75-char fold threshold
        result = format_display_address(long_name, "user@example.com")
        assert "\r" not in result, f"CR found in result: {result!r}"
        assert "\n" not in result, f"LF found in result: {result!r}"
        assert "<user@example.com>" in result

    def test_long_cjk_name_passes_safe_header_check(self):
        """Building a message with a long non-ASCII from_name must not raise ValueError."""
        from gmail.gmail_tools import _prepare_gmail_message

        plain, html = _new_bodies("Hello")
        long_name = "山" * 120
        # Must not raise
        raw, *_ = _prepare_gmail_message(
            subject="Test",
            body=plain,
            html_body=html,
            to="ada@example.com",
            from_email="grace@example.org",
            from_name=long_name,
            web_compose=True,
        )
        msg = _decode_raw(raw)
        assert "From:" in msg


class TestChooseCteUnderscore:
    """choose_cte must classify underscore-heavy ASCII as quoted-printable."""

    def test_underscore_heavy_returns_qp(self):
        from gmail.gmail_web_mime import choose_cte

        result = choose_cte("_" * 200)
        assert result == "quoted-printable", (
            f"Expected 'quoted-printable' for underscore-heavy ASCII, got {result!r}"
        )


class TestNonAsciiSubjectEncoding:
    """Non-ASCII Subject headers must be RFC 2047-encoded; ASCII subjects unchanged."""

    def test_non_ascii_subject_produces_encoded_word(self):
        from gmail.gmail_tools import _prepare_gmail_message

        raw, *_ = _prepare_gmail_message(
            subject="Hello \u2014 world",
            body="body",
            to="to@example.com",
            from_email="from@example.com",
            web_compose=True,
        )
        msg = _decode_raw(raw)
        subject_line = next(
            (line for line in msg.splitlines() if line.startswith("Subject:")), None
        )
        assert subject_line is not None, "No Subject header found"
        # Must be ASCII-only (no raw em-dash)
        assert subject_line.isascii(), (
            f"Subject line contains non-ASCII: {subject_line!r}"
        )
        # Must contain an RFC 2047 encoded-word
        assert "=?" in subject_line and "?=" in subject_line, (
            f"Subject is not RFC 2047-encoded: {subject_line!r}"
        )
        # Raw em-dash must not appear
        assert "\u2014" not in subject_line

    def test_ascii_subject_is_unchanged(self):
        from gmail.gmail_tools import _prepare_gmail_message

        raw, *_ = _prepare_gmail_message(
            subject="Plain subject",
            body="body",
            to="to@example.com",
            from_email="from@example.com",
            web_compose=True,
        )
        msg = _decode_raw(raw)
        subject_line = next(
            (line for line in msg.splitlines() if line.startswith("Subject:")), None
        )
        assert subject_line is not None
        assert subject_line == "Subject: Plain subject"

    def test_non_ascii_subject_round_trips(self):
        from email.header import decode_header
        from gmail.gmail_tools import _prepare_gmail_message

        original = "Hello \u2014 world"
        raw, *_ = _prepare_gmail_message(
            subject=original,
            body="body",
            to="to@example.com",
            from_email="from@example.com",
            web_compose=True,
        )
        msg = _decode_raw(raw)
        subject_line = next(
            (line for line in msg.splitlines() if line.startswith("Subject:")), None
        )
        assert subject_line is not None
        encoded_value = subject_line[len("Subject:") :].strip()
        decoded_parts = decode_header(encoded_value)
        decoded = "".join(
            part.decode(charset or "utf-8") if isinstance(part, bytes) else part
            for part, charset in decoded_parts
        )
        assert decoded == original, f"Round-trip failed: {decoded!r} != {original!r}"


class TestQpCanonicalLineEndings:
    """QP soft breaks must be CRLF even when the part has no newline."""

    def test_long_single_line_has_no_bare_lf(self):
        from gmail.gmail_web_mime import _qp_encode

        encoded = _qp_encode("<div>" + "a" * 200 + "</div>")
        assert "=\r\n" in encoded
        assert "\n" not in encoded.replace("\r\n", "")

    def test_long_single_line_html_part_in_message_has_no_bare_lf(self):
        from gmail.gmail_tools import _prepare_gmail_message

        raw, *_ = _prepare_gmail_message(
            subject="Long line",
            body="x" * 300,
            to="to@example.com",
            from_email="from@example.com",
            web_compose=True,
        )
        msg = _decode_raw(raw)
        assert "\n" not in msg.replace("\r\n", "")
        assert "x" * 300 in _decode_qp_parts(msg)


class TestNonAsciiRecipientEncoding:
    """Non-ASCII recipient display names must be RFC 2047 encoded."""

    def _header(self, msg: str, name: str) -> str:
        return next(line for line in msg.split("\r\n") if line.startswith(f"{name}:"))

    def _build(self, **kwargs):
        from gmail.gmail_tools import _prepare_gmail_message

        raw, *_ = _prepare_gmail_message(
            subject="Hi",
            body="body",
            from_email="from@example.com",
            web_compose=True,
            **kwargs,
        )
        return _decode_raw(raw)

    def test_to_cc_bcc_names_are_encoded_and_round_trip(self):
        from email.header import decode_header, make_header

        msg = self._build(
            to="José Núñez <jose@example.com>, ada@example.com",
            cc="Adá Lóvelace <ada@example.org>",
            bcc="דנה <dana@example.com>",
        )
        for name, expected in (
            ("To", "José Núñez <jose@example.com>, ada@example.com"),
            ("Cc", "Adá Lóvelace <ada@example.org>"),
            ("Bcc", "דנה <dana@example.com>"),
        ):
            line = self._header(msg, name)
            assert line.isascii(), line
            value = line[len(name) + 1 :].strip()
            assert str(make_header(decode_header(value))) == expected

    def test_ascii_recipients_are_unchanged(self):
        value = '"Lovelace, Ada" <ada@example.com>, grace@example.org'
        msg = self._build(to=value)
        assert self._header(msg, "To") == f"To: {value}"

    def test_line_break_in_non_ascii_recipient_is_rejected(self):
        import pytest

        with pytest.raises(ValueError, match="line breaks"):
            self._build(to="José <jose@example.com>\r\nBcc: evil@example.com")


class TestFormatAddressListParsing:
    """format_address_list must never emit empty entries or flatten groups."""

    def _decoded(self, value: str) -> str:
        from email.header import decode_header, make_header

        from gmail.gmail_web_mime import format_address_list

        out = format_address_list(value)
        assert out.isascii(), out
        return str(make_header(decode_header(out)))

    def test_trailing_comma_keeps_the_recipient(self):
        assert self._decoded("José <jose@example.com>,") == "José <jose@example.com>"

    def test_empty_list_element_is_dropped_without_dangling_separator(self):
        assert (
            self._decoded("José <jose@example.com>,, ada@example.com")
            == "José <jose@example.com>, ada@example.com"
        )

    def test_group_syntax_is_preserved(self):
        assert (
            self._decoded("Team: José <j@example.com>, a@example.com;, o@example.com")
            == "Team: José <j@example.com>, a@example.com;, o@example.com"
        )

    def test_non_ascii_group_name_is_encoded(self):
        from email.header import decode_header, make_header

        from gmail.gmail_web_mime import format_address_list

        out = format_address_list("Équipe: a@example.com;, José <j@example.com>")
        assert out.isascii(), out
        group, rest = out.split(": a@example.com;, ", 1)
        assert str(make_header(decode_header(group))) == "Équipe"
        assert str(make_header(decode_header(rest))) == "José <j@example.com>"

    def test_malformed_entry_is_rejected_not_dropped(self):
        import pytest

        from gmail.gmail_web_mime import format_address_list

        with pytest.raises(ValueError, match="Invalid address list"):
            format_address_list("José <jose@example.com>, broken <, ada@example.com")

    def test_name_without_address_is_rejected(self):
        import pytest

        from gmail.gmail_web_mime import format_address_list

        with pytest.raises(ValueError, match="Invalid address list"):
            format_address_list("José")

    def test_empty_group_is_kept(self):
        assert self._decoded("Undisclosed:;, José <j@example.com>") == (
            "Undisclosed:;, José <j@example.com>"
        )

    def test_truncated_angle_address_raises_value_error(self):
        import pytest

        from gmail.gmail_web_mime import format_address_list, parse_address_list

        # The stdlib parser raises IndexError on these; the contract is ValueError.
        for value in ("José <", "é: <"):
            with pytest.raises(ValueError, match="Invalid address list"):
                format_address_list(value)
        for value in ("José <", "a <", "é: <"):
            with pytest.raises(ValueError, match="Invalid address list"):
                parse_address_list(value)

    def test_folded_names_use_crlf_not_bare_lf(self):
        from gmail.gmail_web_mime import format_address_list

        # Long enough that the RFC 2047 encoding folds past the 998-char limit.
        long_name = "é" * 400
        for value in (
            f"{long_name} <j@example.com>",
            f"{long_name}: a@example.com;",
        ):
            out = format_address_list(value)
            assert "\r\n" in out, out
            assert "\n" not in out.replace("\r\n", ""), out

    def test_malformed_ascii_entry_is_rejected(self):
        import pytest

        from gmail.gmail_web_mime import format_address_list

        for value in (
            "alice@example.com, broken <, bob@example.com",
            "ada@example.com; alan@example.com",
            "Ada",
        ):
            with pytest.raises(ValueError, match="Invalid address list"):
                format_address_list(value)

    def test_valid_ascii_list_is_returned_byte_identical(self):
        from gmail.gmail_web_mime import format_address_list

        for value in (
            "Ada Lovelace <ada@example.com>,  alan@example.com",
            "Team: a@example.com;, o@example.com",
            "ada@example.com,,grace@example.com",
        ):
            assert format_address_list(value) == value
