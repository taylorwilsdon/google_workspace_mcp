"""Tests for Gmail-web faithful MIME construction.

All fixtures use synthetic data only (example.com / example.org addresses and
generic names). No real personal data appears in this file.
"""

import base64
import re
from datetime import datetime, timezone
from email import message_from_string
from email.header import decode_header, make_header

import pytest

from gmail.gmail_helpers import (
    _build_web_reply_bodies,
    _derive_web_bodies,
    _prepare_gmail_message_web,
)
from gmail.gmail_web_mime import (
    BLOCKQUOTE_STYLE,
    _qp_encode,
    base_text_direction,
    build_quote_container_html,
    build_quote_html,
    build_quote_plain,
    choose_cte,
    format_address_list,
    format_attribution_html,
    format_attribution_plain,
    format_display_address,
    gmail_boundary,
    new_message_html,
    parse_address_list,
    plain_body_to_html,
)

BOUNDARY_RE = re.compile(r"^0{12}[0-9a-f]{16}$")
INJECTED = "ada@example.com\r\nBcc: evil@example.com"


def _build(**kwargs) -> str:
    """Assemble a web message and return it decoded from base64url."""
    fields = dict(
        subject="Project sync",
        plain_body="Hello there",
        html_body=new_message_html(plain_body_to_html("Hello there")),
        to="Ada Lovelace <ada@example.com>",
        from_email="grace@example.org",
        from_name="Grace Hopper",
    )
    fields.update(kwargs)
    raw = _prepare_gmail_message_web(**fields)
    return base64.urlsafe_b64decode(raw.encode()).decode("utf-8")


def _header(msg: str, name: str) -> str:
    return next(line for line in msg.split("\r\n") if line.startswith(f"{name}:"))


def _part(msg: str, content_type: str) -> str:
    """Return a body part decoded from whichever CTE it was sent with."""
    parsed = message_from_string(msg)
    part = next(p for p in parsed.walk() if p.get_content_type() == content_type)
    return part.get_payload(decode=True).decode("utf-8")


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

    @pytest.mark.parametrize("name", [None, "", "  "])
    def test_no_name_returns_bare_address(self, name):
        assert format_display_address(name, "ada@example.com") == "ada@example.com"

    def test_name_with_comma_is_quoted(self):
        result = format_display_address("Lovelace, Ada", "ada@example.com")
        assert result == '"Lovelace, Ada" <ada@example.com>'

    def test_non_ascii_name_rfc2047_encoded(self):
        result = format_display_address("Adá Lóvelace", "ada@example.com")
        assert result.isascii()
        assert "=?utf-8?" in result.lower()
        assert result.endswith("<ada@example.com>")

    def test_long_non_ascii_name_uses_legal_encoded_words(self):
        result = format_display_address("山" * 120, "user@example.com")
        assert all(
            len(word) <= 75
            for word in re.findall(r"=\?[^?]+\?[bBqQ]\?[^?]*\?=", result)
        )
        assert (
            str(make_header(decode_header(result)))
            == "山" * 120 + " <user@example.com>"
        )
        assert result.endswith("<user@example.com>")

    @pytest.mark.parametrize("name", [None, "Ada", "Adá"])
    def test_crlf_in_address_is_stripped(self, name):
        out = format_display_address(name, INJECTED)
        assert "\r" not in out and "\n" not in out


class TestBaseTextDirection:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Hello world", "ltr"),
            ("שלום עולם", "rtl"),
            ("مرحبا بالعالم", "rtl"),
            # Leading bidi-neutral characters are skipped.
            ('  "123" שלום', "rtl"),
            # The first strong character decides, hence the explicit override.
            ("OK שלום עולם", "ltr"),
            ("123 $ ₪ !!!", "ltr"),
            ("", "ltr"),
        ],
    )
    def test_first_strong_character_decides(self, text, expected):
        assert base_text_direction(text) == expected


class TestNewMessageHtml:
    def test_default_is_ltr(self):
        assert new_message_html("<div>Hi</div>") == '<div dir="ltr"><div>Hi</div></div>'

    def test_explicit_rtl(self):
        assert (
            new_message_html("<div>שלום</div>", direction="rtl")
            == '<div dir="rtl"><div>שלום</div></div>'
        )

    def test_plain_body_to_html_types_each_line(self):
        assert plain_body_to_html("Line one\n\nA & <b>") == (
            "<div>Line one</div><div><br></div><div>A &amp; &lt;b&gt;</div>"
        )


class TestAttribution:
    def test_plain_attribution_no_zero_pad(self):
        dt = datetime(2026, 4, 7, 9, 5, tzinfo=timezone.utc)
        attr = format_attribution_plain("Grace Hopper", "grace@example.org", dt)
        # Day 7 and hour 9 are NOT zero-padded; minute IS.
        assert (
            attr
            == "On Tue, 7 Apr 2026 at 9:05, Grace Hopper <grace@example.org> wrote:"
        )

    def test_html_attribution_structure(self):
        dt = datetime(2026, 4, 7, 19, 19, tzinfo=timezone.utc)
        attr = format_attribution_html("Grace Hopper", "grace@example.org", dt)
        assert attr == (
            '<div dir="ltr" class="gmail_attr">On Tue, 7 Apr 2026 at 19:19, '
            'Grace Hopper &lt;<a href="mailto:grace@example.org">'
            "grace@example.org</a>&gt; wrote:<br></div>"
        )


class TestQuote:
    def test_plain_quote_prefixes_each_line(self):
        assert build_quote_plain("hello\nworld") == "> hello\n> world"

    def test_plain_quote_blank_line_has_no_trailing_space(self):
        assert build_quote_plain("a\n\nb") == "> a\n>\n> b"

    def test_blockquote_uses_gmail_style(self):
        assert BLOCKQUOTE_STYLE == (
            "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);"
            "padding-left:1ex"
        )
        assert build_quote_html("<div>P</div>") == (
            f'<blockquote class="gmail_quote" style="{BLOCKQUOTE_STYLE}">'
            "<div>P</div></blockquote>"
        )

    def test_container_wraps_attribution_and_blockquote(self):
        container = build_quote_container_html("<attr>", "<div>P</div>")
        assert container == (
            '<div class="gmail_quote gmail_quote_container"><attr>'
            f"{build_quote_html('<div>P</div>')}</div>"
        )


class TestEncoding:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("", "quoted-printable"),
            ("Hello there, plain ASCII.", "quoted-printable"),
            ("_" * 200, "quoted-printable"),
            ("שלום עולם, מה שלומך היום?" * 5, "base64"),
        ],
    )
    def test_choose_cte_picks_the_smaller_encoding(self, text, expected):
        assert choose_cte(text) == expected

    def test_qp_soft_breaks_are_crlf_on_a_single_line(self):
        encoded = _qp_encode("<div>" + "a" * 200 + "</div>")
        assert "=\r\n" in encoded
        assert "\n" not in encoded.replace("\r\n", "")

    def test_qp_turns_a_bare_cr_into_a_line_break(self):
        assert _qp_encode("a\rb") == "a\r\nb"


class TestPrepareWebMessage:
    def test_multipart_alternative_plain_then_html(self):
        msg = _build()
        boundary = re.search(r'multipart/alternative; boundary="([^"]+)"', msg)
        assert boundary and BOUNDARY_RE.match(boundary.group(1))
        plain_idx = msg.index('Content-Type: text/plain; charset="UTF-8"')
        html_idx = msg.index('Content-Type: text/html; charset="UTF-8"')
        assert plain_idx < html_idx
        assert msg.count("Content-Transfer-Encoding: quoted-printable") == 2

    def test_header_order_and_display_names(self):
        msg = _build(cc="Charles Babbage <charles@example.org>")
        headers = msg.split("\r\n\r\n", 1)[0].split("\r\n")
        assert [h.split(":", 1)[0] for h in headers] == [
            "MIME-Version",
            "Subject",
            "From",
            "To",
            "Cc",
            "Content-Type",
        ]
        assert "From: Grace Hopper <grace@example.org>" in headers
        assert "To: Ada Lovelace <ada@example.com>" in headers
        assert "Cc: Charles Babbage <charles@example.org>" in headers

    def test_no_name_falls_back_to_bare_addresses(self):
        msg = _build(from_name=None, to="ada@example.com")
        assert _header(msg, "From") == "From: grace@example.org"
        assert _header(msg, "To") == "To: ada@example.com"

    def test_no_vendor_headers_or_message_id(self):
        headers = _build().split("\r\n\r\n", 1)[0].lower()
        for name in ("x-mailer", "user-agent", "message-id"):
            assert name not in headers

    def test_reply_headers_and_re_prefix(self):
        msg = _build(
            in_reply_to="<parent@example.com>",
            references="<root@example.com> <parent@example.com>",
        )
        assert _header(msg, "Subject") == "Subject: Re: Project sync"
        assert _header(msg, "In-Reply-To") == "In-Reply-To: <parent@example.com>"
        assert "References: <root@example.com>\r\n\t<parent@example.com>" in msg

    def test_existing_re_prefix_is_not_doubled(self):
        msg = _build(subject="RE: Project sync", in_reply_to="<p@example.com>")
        assert _header(msg, "Subject") == "Subject: RE: Project sync"

    def test_ascii_subject_is_unchanged(self):
        assert _header(_build(subject="Plain subject"), "Subject") == (
            "Subject: Plain subject"
        )

    @pytest.mark.parametrize("subject", ["Hello é world", "נושא ארוך מאוד " * 60])
    def test_non_ascii_subject_is_encoded_and_round_trips(self, subject):
        msg = _build(subject=subject)
        headers = msg.split("\r\n\r\n", 1)[0]
        assert headers.isascii()
        value = re.search(r"^Subject: (.*?)\r\n(?=\S)", headers, re.S | re.M)
        assert str(make_header(decode_header(value.group(1)))) == subject

    def test_long_non_ascii_from_name_is_accepted(self):
        assert "From:" in _build(from_name="山" * 120)

    @pytest.mark.parametrize("field", ["subject", "to", "cc", "bcc", "in_reply_to"])
    def test_line_break_in_header_is_rejected(self, field):
        with pytest.raises(ValueError, match="line breaks"):
            _build(**{field: INJECTED})

    def test_non_ascii_recipients_are_encoded_and_round_trip(self):
        msg = _build(
            to="José Núñez <jose@example.com>, ada@example.com",
            cc="Adá Lóvelace <ada@example.org>",
            bcc="דנה <dana@example.com>",
        )
        for name, expected in (
            ("To", "José Núñez <jose@example.com>, ada@example.com"),
            ("Cc", "Adá Lóvelace <ada@example.org>"),
            ("Bcc", "דנה <dana@example.com>"),
        ):
            line = _header(msg, name)
            assert line.isascii(), line
            value = line[len(name) + 1 :].strip()
            assert str(make_header(decode_header(value))) == expected

    def test_ascii_recipients_are_unchanged(self):
        value = '"Lovelace, Ada" <ada@example.com>, grace@example.org'
        assert _header(_build(to=value), "To") == f"To: {value}"

    def test_long_single_line_part_has_no_bare_lf(self):
        msg = _build(plain_body="x" * 300, html_body="y" * 300)
        assert "\n" not in msg.replace("\r\n", "")
        assert _part(msg, "text/plain") == "x" * 300
        assert _part(msg, "text/html") == "y" * 300


class TestDeriveWebBodies:
    def test_plain_body_becomes_typed_divs(self):
        plain, html = _derive_web_bodies("Line one\n\nLine three", "plain", "auto")
        assert plain == "Line one\n\nLine three"
        assert html == (
            '<div dir="ltr"><div>Line one</div><div><br></div>'
            "<div>Line three</div></div>"
        )

    def test_html_body_gets_plain_part_with_block_breaks(self):
        plain, html = _derive_web_bodies("<p>One</p><p>Two</p>", "html", "auto")
        assert plain == "One\nTwo"
        assert html == '<div dir="ltr"><p>One</p><p>Two</p></div>'

    @pytest.mark.parametrize(
        "body, direction, expected",
        [
            ("שלום עולם", "auto", "rtl"),
            ("Hello there", "auto", "ltr"),
            ("OK שלום עולם", "rtl", "rtl"),
            ("שלום עולם", "ltr", "ltr"),
        ],
    )
    def test_direction(self, body, direction, expected):
        _, html = _derive_web_bodies(body, "plain", direction)
        assert html.startswith(f'<div dir="{expected}">')

    @pytest.mark.parametrize(
        "body, direction, expected",
        [
            ('<div dir="rtl">Hello</div>', "auto", '<div dir="rtl">Hello</div>'),
            ('<div dir="rtl">Hello</div>', "ltr", '<div dir="ltr">Hello</div>'),
            (
                "  <div dir='ltr' class=x>Hi</div>",
                "rtl",
                '  <div dir="rtl" class=x>Hi</div>',
            ),
            ('<DIV DIR="rtl">Hello</DIV>', "ltr", '<DIV DIR="ltr">Hello</DIV>'),
            (
                '<div class="x" dir="rtl">Hello</div>',
                "ltr",
                '<div class="x" dir="ltr">Hello</div>',
            ),
            (
                '<div title="a dir=rtl b" dir="ltr">Hi</div>',
                "rtl",
                '<div title="a dir=rtl b" dir="rtl">Hi</div>',
            ),
        ],
    )
    def test_existing_dir_wrapper_is_kept_or_overridden(
        self, body, direction, expected
    ):
        assert _derive_web_bodies(body, "html", direction)[1] == expected

    def test_data_dir_attribute_is_not_a_dir_wrapper(self):
        _, html = _derive_web_bodies('<div data-dir="rtl">Hi</div>', "html", "ltr")
        assert html == '<div dir="ltr"><div data-dir="rtl">Hi</div></div>'


class TestWebReplyBodies:
    def _bodies(self, **target):
        base = {"from": "Ada Lovelace <ada@example.com>"}
        base.update(target)
        return _build_web_reply_bodies("Reply", "<div>Reply</div>", base)

    def test_trail_is_appended_to_both_parts(self):
        plain, html = self._bodies(
            date="Tue, 7 Apr 2026 19:19:00 +0000",
            text_body="Original line",
            html_body="<div>Original line</div>",
        )
        attr = "On Tue, 7 Apr 2026 at 19:19, Ada Lovelace"
        assert plain == f"Reply\n\n{attr} <ada@example.com> wrote:\n\n> Original line"
        assert html.startswith("<div>Reply</div><br>")
        assert f"{build_quote_html('<div>Original line</div>')}</div>" in html

    def test_attribution_keeps_parent_date_offset(self):
        plain, html = self._bodies(
            date="Fri, 27 Mar 2026 22:00:00 -0400", text_body="Hi"
        )
        assert "On Fri, 27 Mar 2026 at 22:00, Ada Lovelace" in plain
        assert "On Fri, 27 Mar 2026 at 22:00, Ada Lovelace" in html

    def test_html_only_parent_keeps_line_breaks_in_plain_quote(self):
        plain, _ = self._bodies(
            date="Fri, 27 Mar 2026 10:00:00 +0000",
            html_body="<p>First paragraph.</p><p>Second paragraph.</p>",
        )
        assert "> First paragraph.\n" in plain
        assert "> Second paragraph." in plain

    @pytest.mark.parametrize(
        "target",
        [
            {"from": "Ada Lovelace", "date": "Fri, 27 Mar 2026 10:00:00 +0000"},
            {"date": "not a date"},
        ],
    )
    def test_unusable_parent_leaves_bodies_unchanged(self, target):
        assert self._bodies(**target) == ("Reply", "<div>Reply</div>")


class TestFormatAddressListParsing:
    """format_address_list must never emit empty entries or flatten groups."""

    def _decoded(self, value: str) -> str:
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
        out = format_address_list("Équipe: a@example.com;, José <j@example.com>")
        assert out.isascii(), out
        group, rest = out.split(": a@example.com;, ", 1)
        assert str(make_header(decode_header(group))) == "Équipe"
        assert str(make_header(decode_header(rest))) == "José <j@example.com>"

    def test_empty_group_is_kept(self):
        assert self._decoded("Undisclosed:;, José <j@example.com>") == (
            "Undisclosed:;, José <j@example.com>"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "José <jose@example.com>, broken <, ada@example.com",
            "José",
            "alice@example.com, broken <, bob@example.com",
            "ada@example.com; alan@example.com",
            "Ada",
            # The stdlib parser raises IndexError on these; the contract is ValueError.
            "José <",
            "é: <",
        ],
    )
    def test_malformed_list_is_rejected_not_dropped(self, value):
        with pytest.raises(ValueError, match="Invalid address list"):
            format_address_list(value)

    def test_parse_address_list_rejects_truncated_angle_address(self):
        for value in ("José <", "a <", "é: <"):
            with pytest.raises(ValueError, match="Invalid address list"):
                parse_address_list(value)

    def test_folded_names_use_crlf_not_bare_lf(self):
        # Long names need multiple RFC 2047 encoded words.
        long_name = "é" * 400
        for value in (f"{long_name} <j@example.com>", f"{long_name}: a@example.com;"):
            out = format_address_list(value)
            assert "\r\n" in out, out
            assert "\n" not in out.replace("\r\n", ""), out

    @pytest.mark.parametrize(
        "value",
        [
            "Ada Lovelace <ada@example.com>,  alan@example.com",
            "Team: a@example.com;, o@example.com",
            "ada@example.com,,grace@example.com",
        ],
    )
    def test_valid_ascii_list_is_returned_byte_identical(self, value):
        assert format_address_list(value) == value


class TestFilenameHeaderSafety:
    def test_escape_filename_strips_crlf_and_nul(self):
        from gmail.gmail_web_mime import _escape_filename

        out = _escape_filename("a\r\nX-Evil: 1\x00.pdf")
        assert "\r" not in out and "\n" not in out and "\x00" not in out

    def test_attachment_part_no_header_injection_via_filename(self):
        from gmail.gmail_web_mime import _attachment_part

        part = _attachment_part(
            "BND", "x\r\nBcc: evil@example.com\r\n.pdf", "application/pdf", b"data"
        )
        # The injection vector is a CRLF that starts a new header line; after
        # stripping, the filename text stays on its own header line (harmless).
        assert "\r\nBcc:" not in part
        assert "\rBcc:" not in part and "\nBcc:" not in part

    def test_inline_part_no_injection_via_content_id(self):
        from gmail.gmail_web_mime import _inline_part

        part = _inline_part("BND", "img.png", "image/png", b"d", "cid\r\nX-Evil: 1")
        assert "\r\nX-Evil" not in part
        assert "\rX-Evil" not in part and "\nX-Evil" not in part

    def test_inline_part_x_attachment_id_matches_content_id(self):
        from gmail.gmail_web_mime import _inline_part

        part = _inline_part("BND", "img.png", "image/png", b"d", "ii_abc")
        assert "Content-ID: <ii_abc>\r\nX-Attachment-Id: ii_abc\r\n" in part

    def test_ascii_filename_is_byte_identical_quoted_string(self):
        from gmail.gmail_web_mime import _attachment_part

        part = _attachment_part("BND", "report.pdf", "application/pdf", b"d")
        assert 'name="report.pdf"' in part
        assert 'filename="report.pdf"' in part

    def test_non_ascii_filename_uses_rfc2231(self):
        from gmail.gmail_web_mime import _attachment_part

        part = _attachment_part("BND", "café.pdf", "application/pdf", b"d")
        # No raw non-ASCII bytes leak into the header; RFC 2231 extended params used.
        assert "café.pdf" not in part
        assert "name*=UTF-8''caf%C3%A9.pdf" in part
        assert "filename*=UTF-8''caf%C3%A9.pdf" in part


class TestPartMimeTypeSanitization:
    """A caller-supplied mime_type must not inject headers into a MIME part."""

    INJECTED = "application/pdf\r\nContent-Disposition: inline\r\n\r\nINJECTED"

    def test_attachment_part_rejects_crlf_mime_type(self):
        from gmail.gmail_web_mime import _attachment_part

        part = _attachment_part("b", "a.pdf", self.INJECTED, b"x")
        assert "INJECTED" not in part
        assert 'Content-Type: application/octet-stream; name="a.pdf"' in part

    def test_inline_part_rejects_crlf_mime_type(self):
        from gmail.gmail_web_mime import _inline_part

        part = _inline_part("b", "a.png", self.INJECTED, b"x", "img1")
        assert "INJECTED" not in part
        assert 'Content-Type: application/octet-stream; name="a.png"' in part

    def test_valid_mime_type_kept_and_trimmed(self):
        from gmail.gmail_web_mime import _attachment_part

        part = _attachment_part("b", "a.pdf", "  application/pdf ", b"x")
        assert 'Content-Type: application/pdf; name="a.pdf"' in part

    def test_end_to_end_web_message_has_no_injected_headers(self):
        from gmail.gmail_helpers import _prepare_gmail_message_web

        raw_b64 = _prepare_gmail_message_web(
            subject="s",
            plain_body="p",
            html_body="<div>p</div>",
            to="r@example.com",
            from_email="s@example.com",
            attachment_parts=[
                {"filename": "a.pdf", "mime_type": self.INJECTED, "data": b"x"}
            ],
        )
        raw = base64.urlsafe_b64decode(raw_b64).decode()
        assert "INJECTED" not in raw
        assert "Content-Disposition: inline" not in raw
