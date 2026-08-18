"""Tests for Gmail-web faithful MIME construction.

All fixtures use synthetic data only (example.com / example.org addresses and
generic names). No real personal data appears in this file.
"""

import base64
import quopri
import re
from datetime import datetime, timezone

from gmail.gmail_web_mime import (
    BLOCKQUOTE_STYLE,
    base_text_direction,
    build_quote_container_html,
    build_quote_html,
    build_quote_plain,
    format_attribution_html,
    format_attribution_plain,
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


class TestBaseTextDirection:
    def test_english_is_ltr(self):
        assert base_text_direction("Hello world") == "ltr"

    def test_hebrew_is_rtl(self):
        assert base_text_direction("שלום עולם") == "rtl"

    def test_arabic_is_rtl(self):
        assert base_text_direction("مرحبا بالعالم") == "rtl"

    def test_leading_neutrals_skipped_to_first_strong_rtl(self):
        # Quotes, digits and whitespace are bidi-neutral; the first strong char
        # (Hebrew) decides the base direction.
        assert base_text_direction('  "123" שלום') == "rtl"

    def test_leading_ltr_word_in_otherwise_rtl_body_detected_ltr(self):
        # Documents the first-strong-char heuristic's limitation: an English
        # word before any Hebrew makes the base direction LTR (this is why the
        # explicit `direction` override exists).
        assert base_text_direction("OK שלום עולם") == "ltr"

    def test_no_strong_char_defaults_ltr(self):
        assert base_text_direction("123 $ ₪ !!! ---") == "ltr"

    def test_empty_defaults_ltr(self):
        assert base_text_direction("") == "ltr"


class TestNewMessageHtml:
    def test_wraps_in_ltr_div(self):
        assert new_message_html("<div>Hi</div>") == '<div dir="ltr"><div>Hi</div></div>'

    def test_default_direction_is_ltr_byte_identical(self):
        # No direction argument must stay byte-identical to the historical output
        # (existing web-compose fidelity tests assert exact dir="ltr").
        assert new_message_html("<div>Hi</div>") == '<div dir="ltr"><div>Hi</div></div>'

    def test_explicit_rtl_direction(self):
        assert (
            new_message_html("<div>שלום</div>", direction="rtl")
            == '<div dir="rtl"><div>שלום</div></div>'
        )

    def test_explicit_ltr_direction_matches_default(self):
        assert (
            new_message_html("<div>Hi</div>", direction="ltr")
            == '<div dir="ltr"><div>Hi</div></div>'
        )

    def test_plain_body_to_html_escapes_and_wraps_lines(self):
        html = plain_body_to_html("Line one\n\nLine three & <stuff>")
        assert "&amp;" in html
        assert "&lt;stuff&gt;" in html
        assert "<div><br></div>" in html  # blank line


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
        assert 'class="gmail_attr"' in attr
        assert "On Tue, 7 Apr 2026 at 19:19, Grace Hopper" in attr
        assert (
            '&lt;<a href="mailto:grace@example.org">grace@example.org</a>&gt;' in attr
        )
        assert attr.rstrip().endswith("<br></div>")


class TestQuote:
    def test_plain_quote_prefixes_each_line(self):
        quoted = build_quote_plain("hello\nworld")
        assert quoted == "> hello\n> world"

    def test_plain_quote_blank_line_has_no_trailing_space(self):
        quoted = build_quote_plain("a\n\nb")
        assert quoted == "> a\n>\n> b"

    def test_blockquote_skeleton_exact(self):
        parent = "<div>Parent body</div>"
        bq = build_quote_html(parent)
        assert bq == (
            f'<blockquote class="gmail_quote" style="{BLOCKQUOTE_STYLE}">'
            f"{parent}</blockquote>"
        )

    def test_container_has_quote_container_class(self):
        dt = datetime(2026, 4, 7, 19, 19, tzinfo=timezone.utc)
        attr = format_attribution_html("Ada Lovelace", "ada@example.com", dt)
        container = build_quote_container_html(attr, "<div>Parent body</div>")
        assert container.startswith('<div class="gmail_quote gmail_quote_container">')
        assert "Parent body" in container

    def test_blockquote_style_constant(self):
        assert BLOCKQUOTE_STYLE == (
            "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);"
            "padding-left:1ex"
        )


# ---- Assembly-level tests via the sync builder in gmail_tools ----


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


def _reply_bodies(reply="Thanks!", parent_text="Original line", parent_html=None):
    dt = datetime(2026, 4, 7, 19, 19, tzinfo=timezone.utc)
    parent_html = parent_html or "<div>Original line</div>"
    attr_plain = format_attribution_plain("Ada Lovelace", "ada@example.com", dt)
    attr_html = format_attribution_html("Ada Lovelace", "ada@example.com", dt)
    plain = f"{reply}\n\n{attr_plain}\n{build_quote_plain(parent_text)}"
    html = (
        f"{new_message_html(plain_body_to_html(reply))}<br>"
        f"{build_quote_container_html(attr_html, parent_html)}"
    )
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

    def test_auto_direction_hebrew_body_is_rtl(self):
        msg = self._build_autobody("שלום עולם")
        assert '<div dir="rtl">' in msg
        assert '<div dir="ltr">' not in msg

    def test_auto_direction_english_body_stays_ltr(self):
        msg = self._build_autobody("Hello there")
        assert '<div dir="ltr">' in msg
        assert '<div dir="rtl">' not in msg

    def test_explicit_rtl_override_forces_rtl_on_ltr_first_char(self):
        # Body opens with an English word (auto would pick ltr); explicit rtl wins.
        msg = self._build_autobody("OK שלום עולם", direction="rtl")
        assert '<div dir="rtl">' in msg

    def test_explicit_ltr_override_forces_ltr_on_rtl_body(self):
        msg = self._build_autobody("שלום עולם", direction="ltr")
        assert '<div dir="ltr">' in msg
        assert '<div dir="rtl">' not in msg

    def test_reply_quote_in_both_parts(self):
        plain, html = _reply_bodies()
        raw = self._build(
            plain=plain,
            html=html,
            in_reply_to="<parent@example.com>",
            references="<parent@example.com>",
        )
        # Headers are not QP-encoded; assert on the raw form.
        assert "In-Reply-To: <parent@example.com>" in raw
        assert "References: <parent@example.com>" in raw
        # Bodies are QP-encoded (soft-wrapped); decode before content asserts.
        msg = _decode_qp_parts(raw)
        assert "gmail_quote gmail_quote_container" in msg
        assert "On Tue, 7 Apr 2026 at 19:19" in msg
        # Parent html inherited verbatim.
        assert "<div>Original line</div>" in msg
        # Plain-part one-level quote prefixing.
        assert "> Original line" in msg


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


# ---------------------------------------------------------------------------
# Finding #1 — sender name must NOT be contacts/thread-resolved
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Finding #2 — long non-ASCII name must not produce folding newlines
# ---------------------------------------------------------------------------


class TestLongNonAsciiNameNoFolding:
    """format_display_address must not insert CR/LF for long non-ASCII names."""

    def test_long_cjk_name_no_crlf(self):
        """A very long CJK display name must produce no CR or LF in the result."""
        long_name = "山" * 120  # 120 CJK chars — well over the 75-char fold threshold
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


# ---------------------------------------------------------------------------
# Finding #3 — date_str in forwarded HTML must be HTML-escaped
# ---------------------------------------------------------------------------


class TestForwardedDateEscaping:
    """date_str in the forwarded-message attr block must be HTML-escaped."""

    def test_date_str_with_html_chars_is_escaped(self):
        from gmail.gmail_web_mime import build_forwarded_container_html

        malicious_date = '<script>alert("xss")</script>'
        result = build_forwarded_container_html(
            from_name=None,
            from_email="sender@example.com",
            date_str=malicious_date,
            subject="Normal Subject",
            to_rendered="recipient@example.com",
            orig_html="<div>body</div>",
        )
        # Raw tag injection must not appear
        assert "<script>" not in result
        # Escaped form must be present (proves escaping occurred)
        assert "&lt;script&gt;" in result
        assert "&gt;" in result


# ---------------------------------------------------------------------------
# Finding #4 — underscore-heavy ASCII must choose quoted-printable
# ---------------------------------------------------------------------------


class TestChooseCteUnderscore:
    """choose_cte must classify underscore-heavy ASCII as quoted-printable."""

    def test_underscore_heavy_returns_qp(self):
        from gmail.gmail_web_mime import choose_cte

        result = choose_cte("_" * 200)
        assert result == "quoted-printable", (
            f"Expected 'quoted-printable' for underscore-heavy ASCII, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Fix 1 — render_forward_recipients_html: escaped + mailto-linked recipients
# ---------------------------------------------------------------------------


class TestRenderForwardRecipientsHtml:
    """render_forward_recipients_html must produce Gmail-web forwarded-To markup."""

    def test_bare_address_no_display_name(self):
        from gmail.gmail_web_mime import render_forward_recipients_html

        result = render_forward_recipients_html("addr@example.com")
        assert result == '<a href="mailto:addr@example.com">addr@example.com</a>'

    def test_address_with_display_name(self):
        from gmail.gmail_web_mime import render_forward_recipients_html

        result = render_forward_recipients_html('"Jane Roe" <jane@example.com>')
        assert (
            result
            == 'Jane Roe &lt;<a href="mailto:jane@example.com">jane@example.com</a>&gt;'
        )

    def test_multiple_recipients_joined_by_comma_space(self):
        from gmail.gmail_web_mime import render_forward_recipients_html

        result = render_forward_recipients_html("a@example.com, b@example.com")
        assert result == (
            '<a href="mailto:a@example.com">a@example.com</a>, '
            '<a href="mailto:b@example.com">b@example.com</a>'
        )

    def test_injection_regression_display_name_escaped(self):
        from gmail.gmail_web_mime import render_forward_recipients_html

        malicious = '"<img src=x onerror=alert(1)>" <a@b.com>'
        result = render_forward_recipients_html(malicious)
        # Raw unescaped injection tag must not appear
        assert "<img" not in result
        # Escaped form must be present (confirms escaping occurred)
        assert "&lt;img" in result

    def test_empty_string_returns_empty(self):
        from gmail.gmail_web_mime import render_forward_recipients_html

        assert render_forward_recipients_html("") == ""

    def test_build_forwarded_container_uses_mailto_link(self):
        from gmail.gmail_web_mime import (
            build_forwarded_container_html,
            render_forward_recipients_html,
        )

        to_html = render_forward_recipients_html('"X" <x@example.com>')
        result = build_forwarded_container_html(
            from_name="Sender",
            from_email="sender@example.com",
            date_str="Mon, 1 Jan 2024",
            subject="Test",
            to_rendered=to_html,
            orig_html="<div>body</div>",
        )
        assert '<a href="mailto:x@example.com">' in result
        # The raw unescaped angle-bracket tag <x@example.com> must NOT appear
        assert "<x@example.com>" not in result


# ---------------------------------------------------------------------------
# Fix 2 — non-ASCII Subject must be RFC 2047-encoded in _prepare_gmail_message
# ---------------------------------------------------------------------------


class TestNonAsciiSubjectEncoding:
    """Non-ASCII Subject headers must be RFC 2047-encoded; ASCII subjects unchanged."""

    def test_non_ascii_subject_produces_encoded_word(self):
        from gmail.gmail_tools import _prepare_gmail_message

        raw, *_ = _prepare_gmail_message(
            subject="Hello — world",
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
        assert "—" not in subject_line

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

        original = "Hello — world"
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


class TestNormalizeReplySubject:
    """Reply-subject normalization: single Re:, tag-preserving, idempotent."""

    def test_plain_subject_gets_single_re_prefix(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        assert normalize_reply_subject("Project update") == "Re: Project update"

    def test_existing_re_is_not_doubled(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        assert normalize_reply_subject("Re: Project update") == "Re: Project update"

    def test_uppercase_re_is_treated_as_reply(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        assert normalize_reply_subject("RE: Project update") == "RE: Project update"

    def test_list_tag_before_existing_re_is_left_verbatim(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        # The real-world Zendesk case: leading [tag] then an existing Re:/RE:.
        # Must not gain another "Re:" and must keep the tag and ticket marker.
        subject = "[list] Re: RE: Project status [#123]"
        assert normalize_reply_subject(subject) == subject

    def test_list_tag_without_re_gets_single_re_and_keeps_tag(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        assert (
            normalize_reply_subject("[list] Project status [#123]")
            == "Re: [list] Project status [#123]"
        )

    def test_is_idempotent(self):
        from gmail.gmail_web_mime import normalize_reply_subject

        for s in [
            "Project update",
            "Re: Project update",
            "[list] Re: RE: X [#1]",
            "[list] X",
        ]:
            once = normalize_reply_subject(s)
            assert normalize_reply_subject(once) == once
